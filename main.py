# main.py (Day 5 完整版 - 安全防护 + 权限隔离 + 限流 + Rerank评估优化)
import os
# ❌ 已删除 HF_ENDPOINT，不再需要
import glob
import shutil
import json
import time
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser

# === 新增导入 ===
import numpy as np
from sentence_transformers import CrossEncoder

# 审计模块
from audit import log_query, QueryTimer

# 安全防护模块
from guard import sanitize_prompt, validate_output, SYSTEM_PROMPT

# Day 5: 角色权限配置
from roles_config import get_user_permissions, ROLES

# 配置
from config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    KNOWLEDGE_DIR,
    CHROMA_PERSIST_DIR,
)

# ==========================================
# 1. 初始化大模型 (LLM)
# ==========================================
# ==========================================
# 1. 初始化大模型 (LLM) - 带自动降级机制
# ==========================================
from openai import PermissionDeniedError, RateLimitError

# 模型优先级列表（按额度/效果排序，可随时调整）
MODEL_FALLBACK_LIST = [
    "qwen-plus",               # 主力
    "qwen3.7-plus",            # 新一代旗舰
    "qwen-plus-2025-07-28",    # 主力模型快照
    "qwen3.6-plus",
    "glm-5",
    "qwen3-32b",
    "qwen3.5-35b-a3b",
    "glm-4.5-air",
    "qwen3.6-flash",           # 快而便宜，质量略降
    "qwen3.7-flash-2026-07-15",
    "qwen-long",               # 兜底
]


def get_llm(**kwargs):
    """自动探测可用模型，遇到 403/429 秒切下一个"""
    for model_name in MODEL_FALLBACK_LIST:
        try:
            test_llm = ChatOpenAI(
                model=model_name,
                base_url=DASHSCOPE_BASE_URL,
                api_key=DASHSCOPE_API_KEY,
                temperature=0.1,
                **kwargs,          # ← 新增
            )
            test_llm.invoke("ping")
            print(f"✅ 模型 [{model_name}] 额度正常，已激活")
            return ChatOpenAI(
                model=model_name,
                base_url=DASHSCOPE_BASE_URL,
                api_key=DASHSCOPE_API_KEY,
                temperature=0.1,
                **kwargs,          # ← 新增
            )
        except (PermissionDeniedError, RateLimitError):
            print(f"⚠️ 模型 [{model_name}] 额度耗尽或限流，自动切换下一个...")
            continue
        except Exception as e:
            print(f"❌ 模型 [{model_name}] 连接失败: {e}，尝试下一个...")
            continue
    raise RuntimeError("🚨 所有候选模型均不可用，请检查 API Key 或充值额度")

# ✅ 全局使用降级函数获取 LLM
llm = get_llm()

# ==========================================
# 2. Embedding 模型
# ==========================================
embeddings = OpenAIEmbeddings(
    model="text-embedding-v2",
    openai_api_key=DASHSCOPE_API_KEY,
    openai_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    check_embedding_ctx_length=False,
    chunk_size=10,
)

# ==========================================
# 3.  Rerank 精排模型（按截图真实结构调整）
# ==========================================
from config import LOCAL_RERANK_PATH

rerank_model = CrossEncoder(
    LOCAL_RERANK_PATH,
    max_length=512,
    processor_kwargs={"local_files_only": True},
    model_kwargs={"local_files_only": True}
)


# ==========================================
# 4. 文档加载（Day 5 改造：自动打权限标签）
# ==========================================
def load_all_documents():
    """加载 knowledge 文件夹下所有 txt 文件（支持自动编码检测）"""
    txt_files = glob.glob(os.path.join(KNOWLEDGE_DIR, "*.txt"))
    if not txt_files:
        raise FileNotFoundError(f"在 {KNOWLEDGE_DIR} 文件夹下没有找到任何 .txt 文件")

    print(f"发现 {len(txt_files)} 个知识库文件：")
    all_docs = []

    encodings_to_try = ["utf-8", "gbk", "gb18030", "gb2312", "latin-1"]

    for f in txt_files:
        print(f"   - {os.path.basename(f)}")
        loaded = False

        for encoding in encodings_to_try:
            try:
                loader = TextLoader(f, encoding=encoding)
                docs = loader.load()
                #  Day 5: 给每个文档打上 access_level 标签
                file_name = os.path.basename(f)
                access_level = classify_document_access(file_name)
                for doc in docs:
                    doc.metadata["access_level"] = access_level
                    doc.metadata["source_file"] = file_name
                all_docs.extend(docs)
                print(f"     使用 {encoding} 编码读取成功 | 权限级别: {access_level}")
                loaded = True
                break
            except (UnicodeDecodeError, RuntimeError):
                continue

        if not loaded:
            print(f"     警告: 无法读取文件 {os.path.basename(f)}，已跳过")

    if not all_docs:
        raise ValueError("未能成功加载任何文档，请检查 knowledge 文件夹中的文件格式")

    return all_docs


# ==========================================
# 5. 🆕 Day 5: 文档权限分级
# ==========================================
def classify_document_access(file_name: str) -> str:
    """
    根据文件名自动判定文档的权限级别
    - public:       所有人都能看（制度、流程、FAQ）
    - internal:     经理及以上能看（成本表、汇总数据）
    - confidential: 仅管理员能看（个人报销明细、工资单）
    """
    file_lower = file_name.lower()

    # 机密级：含个人敏感信息
    if any(kw in file_lower for kw in ["报销单据", "工资", "薪资", "个人明细"]):
        return "confidential"

    # 内部级：含部门/公司汇总数据
    if any(kw in file_lower for kw in ["成本执行表", "月度成本", "汇总", "预算"]):
        return "internal"

    # 公开级：制度、流程、通用知识
    return "public"


# ==========================================
# 6. 🆕 Day 5: 简易限流器
# ==========================================
class SimpleRateLimiter:
    """
    简易滑动窗口限流器
    - 每个用户每 60 秒最多请求 max_requests 次
    - 纯内存实现，重启后计数清零（够用于单机演示）
    """

    def __init__(self, max_requests: int = 20, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.request_log = defaultdict(list)

    def is_allowed(self, user_id: str) -> bool:
        now = time.time()
        # 清除过期记录
        self.request_log[user_id] = [
            t for t in self.request_log[user_id]
            if now - t < self.window_seconds
        ]
        if len(self.request_log[user_id]) >= self.max_requests:
            return False
        self.request_log[user_id].append(now)
        return True

    def get_remaining(self, user_id: str) -> int:
        now = time.time()
        valid = [t for t in self.request_log[user_id] if now - t < self.window_seconds]
        return max(0, self.max_requests - len(valid))


# 全局限流器实例
rate_limiter = SimpleRateLimiter(max_requests=20, window_seconds=60)


# ==========================================
# 7. 🆕 Day 5: 权限过滤构建器
# ==========================================
def build_access_filter(user_role: str) -> dict:
    """
    根据用户角色，构建 Chroma 的 where 过滤条件
    """
    permissions = get_user_permissions(user_role)

    if "confidential" in permissions and "internal" in permissions and "public" in permissions:
        return {}  # admin: 无过滤

    return {"access_level": {"$in": permissions}}


# ==========================================
# 8. 🆕 Rerank 精排函数
# ==========================================
def rerank_docs(query: str, docs: list, top_n: int = 5) -> list:
    """对粗排召回的文档进行交叉编码器精排"""
    if not docs:
        return []
    contents = [d.page_content for d in docs]
    pairs = [[query, c] for c in contents]
    scores = rerank_model.predict(pairs)
    ranked_indices = np.argsort(scores)[::-1][:top_n]
    return [docs[i] for i in ranked_indices]


# ==========================================
# 9. 知识库处理（Day 5 改造：切分后保留权限标签）
# ==========================================
def split_documents(docs):
    """智能切分：根据文档类型选择不同的切分粒度"""
    all_splits = []

    for doc in docs:
        source = doc.metadata.get("source", "")
        file_name = os.path.basename(source)

        if "报销单据" in file_name or "成本执行表" in file_name or "月度成本" in file_name:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=250,
                chunk_overlap=80,
                separators=[
                    "------------------------------------------------------------",
                    "\n\n", "\n", "。", "；", " ", "",
                ],
            )
            print(f"     {file_name} -> 表格模式 (chunk=250, overlap=80)")
        else:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=300,
                chunk_overlap=50,
                separators=["\n\n", "\n", "。", "；", " ", ""],
            )
            print(f"     {file_name} -> 文本模式 (chunk=300, overlap=50)")

        splits = splitter.split_documents([doc])
        # 🆕 Day 5: 确保切分后的每个 chunk 都保留 access_level
        for split in splits:
            if "access_level" not in split.metadata:
                split.metadata["access_level"] = doc.metadata.get("access_level", "public")
        all_splits.extend(splits)

    print(f"文档切分完成，共 {len(all_splits)} 个片段")
    return all_splits


def build_knowledge_base(force_rebuild=False):
    """构建知识库：加载 -> 智能切分 -> 向量化 -> 持久化"""
    if force_rebuild and os.path.exists(CHROMA_PERSIST_DIR):
        print(f"强制重建模式：正在删除旧知识库 {CHROMA_PERSIST_DIR} ...")
        shutil.rmtree(CHROMA_PERSIST_DIR)

    print("正在构建知识库...")
    documents = load_all_documents()
    docs = split_documents(documents)

    print("正在向量化并存储到本地数据库...")
    vectorstore = Chroma.from_documents(docs, embeddings, persist_directory=CHROMA_PERSIST_DIR)
    print("知识库构建完成，已持久化到本地")
    return vectorstore


def load_knowledge_base():
    """从本地持久化目录加载已有知识库"""
    if not os.path.exists(CHROMA_PERSIST_DIR):
        raise FileNotFoundError(f"知识库目录 {CHROMA_PERSIST_DIR} 不存在，需要重新构建")

    print("正在从本地加载已有知识库...")
    vectorstore = Chroma(persist_directory=CHROMA_PERSIST_DIR, embedding_function=embeddings)
    print("知识库加载完成")
    return vectorstore


# ==========================================
# 10. 构建/加载知识库
# ==========================================
FORCE_REBUILD = False

if FORCE_REBUILD:
    vectorstore = build_knowledge_base(force_rebuild=True)
else:
    try:
        vectorstore = load_knowledge_base()
    except Exception as e:
        print(f"加载知识库失败: {e}")
        print("自动触发重新构建...")
        vectorstore = build_knowledge_base(force_rebuild=True)

# ✅ 核心修改：扩大基础检索器的召回量，为 Rerank 提供候选池
retriever = vectorstore.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 15},
)

# ==========================================
# 11. Prompt + RAG 链
# ==========================================
# ✅ 核心修改：强化 Prompt 约束，提升 faithfulness (降低幻觉)
template = """你是一个专业的企业级财务助手。请严格遵守以下规则：
1. 只能根据下方「参考上下文」回答，绝对不得引入外部知识或主观推测。
2. 如果「参考上下文」中找不到直接答案，请直接回复“知识库中未找到相关内容”，严禁自行编造。
3. 涉及报销标准、流程时，尽量引用条款号或数据来源。

参考上下文：
{context}

用户问题：
{question}

专业回答："""

prompt = ChatPromptTemplate.from_template(template)


def format_docs(docs):
    if not docs:
        return ""
    return "\n\n".join([doc.page_content for doc in docs])


rag_chain = (
        {
            "context": RunnableLambda(lambda x: format_docs(retriever.invoke(x["question"]))),
            "question": RunnableLambda(lambda x: x["question"]),
        }

        | prompt
        | llm
        | StrOutputParser()
)


# ==========================================
# 12. 封装一个可复用的 RAG 函数（评估脚本要用）
# ==========================================
def rag_query(question: str) -> dict:
    """执行一次 RAG，返回答案 + 检索到的上下文（供 RAGAS 评估用）"""
    # ✅ 核心修改：在这里接入 Rerank！解决评估指标低的核心问题
    raw_docs = retriever.invoke(question)

    # 使用 Rerank 模型对粗排的 15 条进行精排，只保留最相关的 Top 5
    docs = rerank_docs(question, raw_docs, top_n=5)

    contexts = [doc.page_content for doc in docs]

    if not docs:
        answer = "抱歉，知识库中未找到相关内容，无法回答该问题。"
    else:
        context_text = "\n\n".join(contexts)
        chain = prompt | llm | StrOutputParser()
        answer = chain.invoke({"context": context_text, "question": question})

    return {"answer": answer, "contexts": contexts}


# ==========================================
# 13. 🆕 Day 5 改造: 带权限隔离 + 限流 + 审计的查询函数
# ==========================================
def ask(question: str, user_role: str = "employee") -> dict:
    """
    带权限隔离、限流、审计、计时的查询接口
    """
    # ---- Step 1: 限流检查 ----
    if not rate_limiter.is_allowed(user_role):
        msg = f"⚠️ 请求过于频繁！您的角色 [{user_role}] 每 60 秒最多 20 次请求，请稍后再试。"
        print(f"[限流器] {msg}")
        return {"answer": msg, "blocked": True, "reason": "rate_limit"}

    # ---- Step 2: 构建权限过滤的检索器 ----
    access_filter = build_access_filter(user_role)

    search_kwargs = {"k": 20}  # 扩大粗排召回量，给 Rerank 提供候选池
    if access_filter:
        search_kwargs["filter"] = access_filter

    filtered_retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs=search_kwargs,
    )

    # 打印权限信息（调试用）
    permissions = get_user_permissions(user_role)
    print(f"[权限系统] 角色={user_role}, 可访问级别={permissions}, 过滤条件={access_filter or '无(全量)'}")

    with QueryTimer() as t:
        # ---- Step 3: 带权限过滤的检索 + Rerank 精排 ----
        raw_docs = filtered_retriever.invoke(question)
        docs = rerank_docs(question, raw_docs, top_n=5)

        chunks_meta = [
            {
                "source": d.metadata.get("source", "unknown"),
                "access_level": d.metadata.get("access_level", "unknown"),
                "score": d.metadata.get("score"),
                "preview": d.page_content[:80],
            }
            for d in docs
        ]

        # ---- Step 4: 生成回答 (✅ 彻底修复版) ----
        context_text = "\n\n".join([doc.page_content for doc in docs]) if docs else ""
        chain = prompt | llm | StrOutputParser()
        # StrOutputParser 直接返回字符串，不再使用 result 变量
        answer = chain.invoke({"context": context_text, "question": question})

        # 防御性检查：确保 answer 一定是字符串
        if not isinstance(answer, str):
            answer = str(answer)

        # ---- Step 5: Token 统计 (✅ 适配纯字符串输出) ----
        # 注意：经过 StrOutputParser 后，usage_metadata 会丢失
        # 这里做安全兜底，避免再次触发 NameError / AttributeError
        p_tok = 0
        c_tok = 0

    # ---- Step 6: 写入审计日志 ----
    log_query(
        user=user_role,           # ← 新增这一行
        question=question,
        answer=answer,
        retrieved_chunks=chunks_meta,
        latency_ms=t.latency_ms,
        prompt_tokens=p_tok,
        completion_tokens=c_tok,
    )

    return {"answer": answer, "blocked": False, "reason": ""}


# ==========================================
# 14. 🆕 Day 5 改造: 交互式问答
# ==========================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("RAG 企业知识助手 (Day 5 - 权限隔离版) 已就绪！")
    print("=" * 60)

    print("\n请选择你的身份：")
    print("  1. admin    - 管理员（可查看所有文档）")
    print("  2. manager  - 经理（可查看公开 + 内部文档）")
    print("  3. employee - 普通员工（仅可查看公开文档）")
    print()

    role_map = {"1": "admin", "2": "manager", "3": "employee"}
    while True:
        choice = input("请输入编号 (1/2/3): ").strip()
        if choice in role_map:
            current_role = role_map[choice]
            break
        else:
            print("无效输入，请输入 1、2 或 3")

    permissions = get_user_permissions(current_role)
    print(f"\n✅ 已登录为: [{current_role}]")
    print(f"   可访问文档级别: {permissions}")
    print(f"   限流: 每 60 秒最多 20 次请求")
    print(f"\n输入你的问题，按 Enter 发送；输入 'quit' 或 'exit' 退出")
    print(f"输入 'role' 可切换身份")
    print("=" * 60 + "\n")

    while True:
        try:
            user_question = input(f"你 [{current_role}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if user_question.lower() in ("quit", "exit", "退出"):
            print("再见！")
            break

        # 切换角色命令
        if user_question.lower() == "role":
            print("\n切换身份：")
            print("  1. admin    2. manager    3. employee")
            new_choice = input("请输入编号: ").strip()
            if new_choice in role_map:
                current_role = role_map[new_choice]
                perms = get_user_permissions(current_role)
                print(f"✅ 已切换为: [{current_role}], 可访问级别: {perms}\n")
            else:
                print("无效输入，保持当前身份\n")
            continue

        if not user_question:
            print("请输入有效问题\n")
            continue

        # 🛡️ 1. 输入净化
        safe_question = sanitize_prompt(user_question)
        print("[安全系统] 您的输入已进行安全净化。")

        print("\nAI: ", end="", flush=True)
        try:
            # 2. 核心查询（带权限隔离 + 限流）
            result = ask(safe_question, user_role=current_role)

            if result["blocked"]:
                print(result["answer"])
            else:
                # 🛡️ 3. 输出校验
                safe_answer = validate_output(result["answer"])
                print(safe_answer)

            # 显示剩余请求次数
            remaining = rate_limiter.get_remaining(current_role)
            print(f"\n[限流器] 剩余请求次数: {remaining}/20")

        except Exception as e:
            print(f"\n请求出错: {e}")
        print("\n" + "-" * 60 + "\n")