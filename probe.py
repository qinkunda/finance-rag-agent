# probe.py — 直接探向量库
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from config import DASHSCOPE_API_KEY, CHROMA_PERSIST_DIR

e = OpenAIEmbeddings(
    model="text-embedding-v2",
    openai_api_key=DASHSCOPE_API_KEY,
    openai_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    check_embedding_ctx_length=False,
)
vs = Chroma(persist_directory=CHROMA_PERSIST_DIR, embedding_function=e)
print("切片总数:", vs._collection.count())

q = "深圳属于几类城市"
print("\n=== 无过滤 top5 ===")
for d in vs.similarity_search(q, k=5):
    print("---", d.metadata.get("access_level"), "|", d.page_content[:80].replace("\n", " "))

print("\n=== employee过滤 top5 ===")
for d in vs.similarity_search(q, k=5, filter={"access_level": {"$in": ["public"]}}):
    print("---", d.metadata.get("access_level"), "|", d.page_content[:80].replace("\n", " "))