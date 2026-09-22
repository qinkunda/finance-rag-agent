"""
RAG 评估脚本 - 完整修复版 v2
修复清单：
1. eval_llm 复用 main.get_llm() 自动降级（原硬编码 qwen-plus，额度耗尽即全灭）
2. import 顺序修正：先 import config，再执行 monkey-patch（原顺序导致 NameError）
3. 补回 from datasets import Dataset（原文件丢失）
4. 函数改名 run()，避免与 import main 冲突
5. build_eval_dataset 并发化（ThreadPoolExecutor, 4 并发）
6. 结果打印适配新版 ragas（EvaluationResult 用 to_pandas 取均值）
运行: python evaluate.py
"""
from concurrent.futures import ThreadPoolExecutor

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
from ragas.llms import LangchainLLMWrapper
from langchain_openai import OpenAIEmbeddings, ChatOpenAI

# ✅ 第一步：先导入配置（patch 里要用到）
from config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
from main import rag_query, get_llm
from eval_dataset import EVAL_DATASET
import main as main_module

# ✅ 第二步：monkey-patch —— 运行时把 main.py 的生成模型换成非思考 flash 模型
#    （qwen3.7-plus 思考模式对查表型问题过度保守，导致错误拒答；flash 不思考、便宜、快）
#    若 qwen3.6-flash 报 403，把 model 改成 "qwen-turbo"（已验证有额度）
main_module.llm = ChatOpenAI(
    model="qwen3.7-plus",
    # ← 改这里
    base_url=DASHSCOPE_BASE_URL,
    api_key=DASHSCOPE_API_KEY,
    temperature=0.1,
    extra_body={"enable_thinking": False},     # ← 加这行
)


def query_one(item):
    """单条问题跑 RAG（并发 worker 调用）"""
    result = rag_query(item["question"])
    return {
        "question": item["question"],
        "answer": result["answer"],
        "contexts": result["contexts"],
        "ground_truth": item["ground_truth"],
    }


def build_eval_dataset():
    """20 条问题并发查询，替代原串行循环"""
    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(query_one, EVAL_DATASET))
    return Dataset.from_list(rows)


def run():
    print("🔍 正在生成评估数据集（并发查询 RAG）...")
    eval_ds = build_eval_dataset()
    print(f"✅ 共 {len(eval_ds)} 条测试数据，开始评估...\n")

    # ✅ 打分 LLM 走自动降级（与问答同一模型接入链路，保证评估一致性）
    eval_llm = get_llm(extra_body={"enable_thinking": False})

    # ✅ Embeddings: 和 main.py 完全一致
    eval_embeddings = OpenAIEmbeddings(
        model="text-embedding-v2",
        openai_api_key=DASHSCOPE_API_KEY,
        openai_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        check_embedding_ctx_length=False,
        chunk_size=10,
    )

    results = evaluate(
        dataset=eval_ds,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=LangchainLLMWrapper(eval_llm),
        embeddings=eval_embeddings,
    )

    # ✅ 新版 ragas：EvaluationResult 不是 dict，用 to_pandas 取数值列均值
    results_df = results.to_pandas()
    numeric_cols = results_df.select_dtypes(include="number").columns
    print("\n📊 评估结果:")
    for col in numeric_cols:
        print(f"  🎯 {col}: {results_df[col].mean():.4f}")

    results_df.to_csv("eval_results.csv", index=False, encoding="utf-8-sig")
    print("\n💾 详细结果已保存到 eval_results.csv")


if __name__ == "__main__":
    run()