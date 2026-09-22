"""
Day 4: 向量数据库存储与检索
目标：把文本和向量存入 Chroma 数据库，并实现相似性检索
"""

import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'  # 防止模型下载卡住

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma

# ============ 第1步：准备数据（复用 Day 2 的逻辑） ============
print("=" * 50)
print("📄 第1步：加载并切分文档")
print("=" * 50)

loader = TextLoader("docs/test.txt", encoding="utf-8")
documents = loader.load()

text_splitter = RecursiveCharacterTextSplitter(chunk_size=200, chunk_overlap=50)
chunks = text_splitter.split_documents(documents)
print(f"✅ 文档已切分为 {len(chunks)} 个片段")

# ============ 第2步：加载本地 Embedding 模型（复用 Day 3 的逻辑） ============
print("\n" + "=" * 50)
print("🧠 第2步：加载 Embedding 模型")
print("=" * 50)

# 指向你电脑里真正的模型路径（请根据你电脑里的实际路径修改！）
local_model_path = r"C:\Users\39034\Desktop\AI_Project\RAG_Demo\models\BAAI--bge-large-zh-v1.5\snapshots\master"

embeddings = HuggingFaceEmbeddings(
    model_name=local_model_path,
    model_kwargs={'device': 'cpu'},  # 强制使用 CPU，避免显卡报错
    encode_kwargs={'normalize_embeddings': True}  # 开启归一化，提高检索精度
)
print("✅ 本地 Embedding 模型加载成功！")

# ============ 第3步：存入 Chroma 向量数据库 ============
print("\n" + "=" * 50)
print("🏦 第3步：存入 Chroma 向量数据库")
print("=" * 50)

# 这一行代码会自动完成：把 chunks 转成向量 -> 存入本地 chroma_db 文件夹
vectorstore = Chroma.from_documents(
    documents=chunks,
    embedding=embeddings,
    persist_directory="./chroma_db"  # 数据库保存在当前文件夹下
)
print("✅ 数据已成功存入本地向量数据库！")

# ============ 第4步：模拟检索（最激动人心的时刻） ============
print("\n" + "=" * 50)
print("🔍 第4步：提问并检索最相关的文本")
print("=" * 50)

question = "什么是向量检索？"
print(f"❓ 用户提问：{question}\n")

# 让数据库去检索最相关的 2 个片段
docs = vectorstore.similarity_search(question, k=2)

print("🎯 检索到的最相关片段：")
for i, doc in enumerate(docs, 1):
    print(f"\n--- 第 {i} 个相关片段 ---")
    print(doc.page_content)

print("\n" + "=" * 50)
print("🎉 Day 4 完成！你已经掌握了 RAG 的检索核心！")
print("=" * 50)