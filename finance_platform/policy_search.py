"""统一制度检索：离线关键词降级与在线 Chroma RAG 共用同一 Chunk、权限和引用契约。"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from roles_config import get_user_permissions
from .ingestion import ROOT, build_corpus

SENSITIVE_TERMS = ("个人明细", "报销单据", "工资", "薪资", "他人报销")


def tokenize(text: str) -> list[str]:
    latin = re.findall(r"[a-z0-9]+", text.lower())
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    bigrams = [chinese[index:index + 2] for index in range(max(0, len(chinese) - 1))]
    return latin + bigrams


class PolicySearch:
    def __init__(self, root: Path = ROOT, db_path: Path | None = None):
        self.root = root
        from .database import database_path
        self.db_path = db_path or database_path(root)
        path = root / "data" / "curated" / "chunks.jsonl"
        if not path.exists():
            build_corpus(root)
        self.chunks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.doc_freq = Counter()
        for chunk in self.chunks:
            self.doc_freq.update(set(tokenize(chunk["text"])))
        self.total = len(self.chunks)
        configured = os.getenv("FINANCE_RETRIEVAL_MODE", "").strip().lower()
        offline = os.getenv("FINANCE_OFFLINE_DEMO", "true").lower() == "true"
        self.mode = configured or ("lexical" if offline else "vector")
        if self.mode not in {"lexical", "vector"}:
            raise ValueError("FINANCE_RETRIEVAL_MODE 仅支持 lexical 或 vector")
        self._vector = None

    @property
    def vector(self):
        if self._vector is None:
            from .vector_rag import VectorKnowledgeBase

            self._vector = VectorKnowledgeBase(self.root)
        return self._vector

    def _document_states(self) -> dict[str, dict[str, Any]]:
        from .database import connect
        path = self.db_path
        if not path.exists():
            return {}
        with connect(path) as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_states'").fetchone()
            if not exists:
                return {}
            return {x["document_id"]: dict(x) for x in conn.execute("SELECT * FROM document_states")}

    def _active_document(self, document_id: str, static_status: str, as_of: str | None,
                         states: dict[str, dict[str, Any]]) -> bool:
        state = states.get(document_id)
        if not state:
            return static_status == "active"
        day = as_of or date.today().isoformat()
        # 未来版本发布会把当前版本标记为 superseded，并把生效日写成旧版本的
        # effective_to；旧版本在该日期前仍必须可检索。pending/archived 不参与检索。
        versioned = state["status"] == "active" or (
            state["status"] == "superseded" and bool(state["effective_to"])
        )
        return (versioned and state["effective_from"] <= day
                and (not state["effective_to"] or day < state["effective_to"]))

    def _score(self, query: str, text: str) -> float:
        query_terms = tokenize(query)
        doc_terms = Counter(tokenize(text))
        if not query_terms or not doc_terms:
            return 0.0
        score = 0.0
        length_norm = 0.75 + 0.25 * sum(doc_terms.values()) / 120
        for term in query_terms:
            tf = doc_terms.get(term, 0)
            if not tf:
                continue
            df = self.doc_freq.get(term, 0)
            idf = math.log(1 + (self.total - df + 0.5) / (df + 0.5))
            score += idf * tf * 2.2 / (tf + 1.2 * length_norm)
        return score

    def _lexical_search(self, question: str, user_role: str, top_k: int, as_of: str | None = None) -> list[dict[str, Any]]:
        permissions = set(get_user_permissions(user_role))
        states = self._document_states()
        candidates = [
            chunk for chunk in self.chunks
            if self._active_document(chunk["document_id"], chunk["status"], as_of, states)
            and chunk["access_level"] in permissions
        ]
        ranked = [(self._score(question, chunk["text"]), chunk) for chunk in candidates]
        ranked = [(score, chunk) for score, chunk in ranked if score > 0.15]
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        results = []
        for score, chunk in ranked[:top_k]:
            results.append({
                "chunk_id": chunk["chunk_id"], "document_id": chunk["document_id"],
                "title": chunk["title"], "version": chunk["version"],
                "effective_date": chunk["effective_date"], "source_path": chunk["source_path"],
                "access_level": chunk["access_level"], "labels": chunk["labels"],
                "score": round(score, 4), "text": chunk["text"],
            })
        return results

    def search(self, question: str, user_role: str = "employee", top_k: int = 3,
               as_of: str | None = None) -> list[dict[str, Any]]:
        question = question.strip()
        if not question:
            raise ValueError("question 不能为空")
        if len(question) > 1000:
            raise ValueError("question 不能超过 1000 字")
        states = self._document_states()
        if self.mode == "vector":
            hits = self.vector.search(question, set(get_user_permissions(user_role)), max(top_k * 3, top_k))
            return [hit for hit in hits if self._active_document(
                hit["document_id"], "active", as_of, states
            )][:top_k]
        return self._lexical_search(question, user_role, top_k, as_of)

    @staticmethod
    def _citations(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {key: hit[key] for key in (
                "document_id", "chunk_id", "title", "version", "effective_date", "source_path",
            )}
            for hit in hits
        ]

    def answer(self, question: str, user_role: str = "employee") -> dict[str, Any]:
        if user_role != "admin" and any(term in question for term in SENSITIVE_TERMS):
            return {
                "answer": "权限不足：该问题涉及个人或敏感明细，已按最小权限原则拦截。",
                "blocked": True, "citations": [], "contexts": [],
                "retrieval_mode": self.mode, "generation_mode": "none",
            }
        hits = self.search(question, user_role)
        if not hits:
            return {
                "answer": "知识库中未找到可回答该问题的有效条款。", "blocked": False,
                "citations": [], "contexts": [], "retrieval_mode": self.mode,
                "generation_mode": "none",
            }
        citations = self._citations(hits)
        contexts = [hit["text"] for hit in hits]
        if self.mode == "vector" and os.getenv("FINANCE_GENERATION_ENABLED", "true").lower() == "true":
            from .vector_rag import generate_answer

            answer = generate_answer(question, hits)
            generation_mode = os.getenv("FINANCE_LLM_MODEL", "qwen-plus")
        else:
            excerpts = []
            for index, hit in enumerate(hits[:3], 1):
                body = hit["text"].split("\n", 1)[-1]
                excerpts.append(f"{index}. {body}（来源：{hit['title']} {hit['version']}，{hit['chunk_id']}）")
            answer = "依据当前生效的合成制度条款：\n" + "\n".join(excerpts)
            generation_mode = "extractive"
        return {
            "answer": answer, "blocked": False, "citations": citations, "contexts": contexts,
            "retrieval_mode": self.mode, "generation_mode": generation_mode,
        }
