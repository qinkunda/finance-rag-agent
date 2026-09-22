"""
Day 2: 文档加载与切分
目标：把一份文档加载进来，切成一小段一小段
"""

from langchain_community.document_loaders import TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter

# ============ 第1步：加载文档 ============
print("=" * 50)
print("📄 第1步：加载文档")
print("=" * 50)

loader = TextLoader("docs/test.txt", encoding="utf-8")
documents = loader.load()

print(f"✅ 加载成功！共 {len(documents)} 个文档")
print(f"📝 文档内容前200字：")
print(documents[0].page_content[:200])
print("...")

# ============ 第2步：切分文档 ============
print("\n" + "=" * 50)
print("✂️ 第2步：切分文档")
print("=" * 50)

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=200,        # 每块最多200个字符
    chunk_overlap=50,      # 相邻块之间重叠50个字符（防止切断语义）
    separators=["\n\n", "\n", "。", "！", "？", " ", ""]  # 按这个优先级切分
)

chunks = text_splitter.split_documents(documents)

print(f"✅ 切分完成！共切成 {len(chunks)} 块")
print()

# ============ 第3步：查看每一块的内容 ============
print("=" * 50)
print("🔍 第3步：查看切分结果")
print("=" * 50)

for i, chunk in enumerate(chunks):
    print(f"\n--- 第 {i+1} 块 ({len(chunk.page_content)} 字) ---")
    print(chunk.page_content)

print("\n" + "=" * 50)
print("🎉 Day 2 完成！文档已成功加载并切分")
print("=" * 50)