"""将统一清洗切分后的全部知识 Chunk 写入 Chroma。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finance_platform.vector_rag import VectorKnowledgeBase


if __name__ == "__main__":
    result = VectorKnowledgeBase().build(rebuild=True)
    print(json.dumps(result, ensure_ascii=False))
