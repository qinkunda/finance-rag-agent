"""统一知识 Chunk 的 Chroma 向量索引、权限过滤、可选 BGE 精排与千问生成。"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .ingestion import ROOT, build_corpus

load_dotenv(ROOT / ".env")


def vector_directory(root: Path = ROOT) -> Path:
    return root / "chroma_db" / "unified"


def dashscope_embeddings():
    from langchain_openai import OpenAIEmbeddings

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("向量检索需要 DASHSCOPE_API_KEY；无 Key 时请设置 FINANCE_OFFLINE_DEMO=true")
    return OpenAIEmbeddings(
        model=os.getenv("FINANCE_EMBEDDING_MODEL", "text-embedding-v2"),
        openai_api_key=api_key,
        openai_api_base=os.getenv(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        check_embedding_ctx_length=False,
        chunk_size=10,
    )


class VectorKnowledgeBase:
    def __init__(
        self, root: Path = ROOT, persist_dir: Path | None = None,
        embeddings: Any | None = None, reranker_path: Path | None = None,
    ):
        self.root = root
        self.persist_dir = persist_dir or vector_directory(root)
        self.embeddings = embeddings
        self.reranker_path = reranker_path
        self._store = None
        self._reranker = None

    def close(self) -> None:
        store, self._store = self._store, None
        if store is not None:
            store._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _embedding(self):
        if self.embeddings is None:
            self.embeddings = dashscope_embeddings()
        return self.embeddings

    def _documents(self) -> list[Document]:
        from langchain_core.documents import Document

        path = self.root / "data" / "curated" / "chunks.jsonl"
        if not path.exists():
            build_corpus(self.root)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        documents = []
        for row in rows:
            metadata = {
                key: row[key] for key in (
                    "chunk_id", "document_id", "title", "scenario", "document_type", "version",
                    "effective_date", "status", "access_level", "source_type", "source_path", "text_sha256",
                )
            }
            metadata["labels"] = ",".join(row.get("labels", []))
            documents.append(Document(page_content=row["text"], metadata=metadata))
        return documents

    def build(self, rebuild: bool = False) -> dict[str, Any]:
        if rebuild:
            self.close()
        if rebuild and self.persist_dir.exists():
            shutil.rmtree(self.persist_dir)
        self.persist_dir.parent.mkdir(parents=True, exist_ok=True)
        documents = self._documents()
        if not documents:
            raise ValueError("没有可向量化的知识 Chunk")
        from langchain_chroma import Chroma

        self._store = Chroma.from_documents(
            documents=documents, embedding=self._embedding(),
            ids=[doc.metadata["chunk_id"] for doc in documents],
            persist_directory=str(self.persist_dir), collection_name="finance_unified",
        )
        persist_text = (
            str(self.persist_dir.relative_to(self.root))
            if self.persist_dir.is_relative_to(self.root) else str(self.persist_dir)
        )
        return {
            "document_count": len({doc.metadata["document_id"] for doc in documents}),
            "chunk_count": len(documents), "vector_count": self.count(),
            "persist_dir": persist_text,
        }

    def load(self):
        if self._store is None:
            if not (self.persist_dir / "chroma.sqlite3").exists():
                raise FileNotFoundError("统一向量库不存在，请先运行 python scripts/build_vector_store.py")
            from langchain_chroma import Chroma

            self._store = Chroma(
                persist_directory=str(self.persist_dir), embedding_function=self._embedding(),
                collection_name="finance_unified",
            )
        return self._store

    def count(self) -> int:
        return int(self.load()._collection.count())

    def _rerank(self, question: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        path = self.reranker_path
        if path is None:
            candidate = self.root / "models" / "BAAI--bge-reranker-v2-m3" / "snapshots" / "master"
            path = candidate if candidate.exists() else None
        if path is None or not path.exists() or os.getenv("FINANCE_ENABLE_RERANK", "true").lower() != "true":
            return hits
        if self._reranker is None:
            from sentence_transformers import CrossEncoder

            self._reranker = CrossEncoder(
                str(path), max_length=512,
                processor_kwargs={"local_files_only": True}, model_kwargs={"local_files_only": True},
            )
        scores = self._reranker.predict([[question, hit["text"]] for hit in hits])
        for hit, score in zip(hits, scores):
            hit["rerank_score"] = float(score)
        return sorted(hits, key=lambda item: item["rerank_score"], reverse=True)

    def search(self, question: str, permissions: set[str], top_k: int = 3) -> list[dict[str, Any]]:
        store = self.load()
        filters: list[dict[str, Any]] = [{"status": "active"}]
        if not {"public", "internal", "confidential"}.issubset(permissions):
            filters.append({"access_level": {"$in": sorted(permissions)}})
        where = filters[0] if len(filters) == 1 else {"$and": filters}
        pairs = store.similarity_search_with_score(
            question, k=max(top_k * 5, 15), filter=where,
        )
        hits = []
        for doc, distance in pairs:
            hit = dict(doc.metadata)
            hit["labels"] = [x for x in hit.get("labels", "").split(",") if x]
            hit["text"] = doc.page_content
            hit["vector_distance"] = round(float(distance), 6)
            hits.append(hit)
        return self._rerank(question, hits)[:top_k]


def generate_answer(question: str, hits: list[dict[str, Any]]) -> str:
    """只依据已检索条款生成；无检索结果时不调用模型。"""
    from langchain_openai import ChatOpenAI

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("千问生成需要 DASHSCOPE_API_KEY")
    context = "\n\n".join(
        f"[{index}] {hit['title']} V{hit['version']} {hit['chunk_id']}\n{hit['text']}"
        for index, hit in enumerate(hits, 1)
    )
    prompt = f"""你是企业财务制度助手。只能根据参考条款回答，不得补充外部知识。
回答应直接、简洁，并在关键结论后标注引用编号，如[1]。找不到依据时明确说未找到。

参考条款：
{context}

用户问题：{question}
"""
    llm = ChatOpenAI(
        model=os.getenv("FINANCE_LLM_MODEL", "qwen-plus"),
        base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        api_key=api_key, temperature=0.1,
    )
    response = llm.invoke(prompt)
    return str(response.content).strip()
