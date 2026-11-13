"""离线、可解释的关键词检索；用于测试和 Dify 无 Key 演示，不替代原 RAGAS 结果。"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from roles_config import get_user_permissions
from .ingestion import ROOT, build_corpus

SENSITIVE_TERMS = ("个人明细", "报销单据", "工资", "薪资", "他人报销")


def tokenize(text: str) -> list[str]:
    latin = re.findall(r"[a-z0-9]+", text.lower())
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    bigrams = [chinese[i:i + 2] for i in range(max(0, len(chinese) - 1))]
    return latin + bigrams


class PolicySearch:
    def __init__(self, root: Path = ROOT):
        self.root = root
        path = root / "data" / "curated" / "chunks.jsonl"
        if not path.exists():
            build_corpus(root)
        self.chunks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.doc_freq = Counter()
        for chunk in self.chunks:
            self.doc_freq.update(set(tokenize(chunk["text"])))
        self.total = len(self.chunks)

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

    def search(self, question: str, user_role: str = "employee", top_k: int = 3) -> list[dict[str, Any]]:
        question = question.strip()
        if not question:
            raise ValueError("question 不能为空")
        if len(question) > 1000:
            raise ValueError("question 不能超过 1000 字")
        permissions = set(get_user_permissions(user_role))
        candidates = [
            chunk for chunk in self.chunks
            if chunk["status"] == "active" and chunk["access_level"] in permissions
        ]
        ranked = [(self._score(question, chunk["text"]), chunk) for chunk in candidates]
        ranked = [(score, chunk) for score, chunk in ranked if score > 0.15]
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        results = []
        for score, chunk in ranked[:top_k]:
            results.append({
                "chunk_id": chunk["chunk_id"],
                "document_id": chunk["document_id"],
                "title": chunk["title"],
                "version": chunk["version"],
                "effective_date": chunk["effective_date"],
                "source_path": chunk["source_path"],
                "access_level": chunk["access_level"],
                "labels": chunk["labels"],
                "score": round(score, 4),
                "text": chunk["text"],
            })
        return results

    def answer(self, question: str, user_role: str = "employee") -> dict[str, Any]:
        if user_role != "admin" and any(term in question for term in SENSITIVE_TERMS):
            return {"answer": "权限不足：该问题涉及个人或敏感明细，已按最小权限原则拦截。", "blocked": True, "citations": [], "contexts": []}
        hits = self.search(question, user_role)
        if not hits:
            return {"answer": "知识库中未找到可回答该问题的有效条款。", "blocked": False, "citations": [], "contexts": []}
        excerpts = []
        for index, hit in enumerate(hits[:2], 1):
            body = hit["text"].split("\n", 1)[-1]
            excerpts.append("{}. {}（来源：{} {}，{}）".format(index, body, hit["title"], hit["version"], hit["chunk_id"]))
        citations = [{k: hit[k] for k in ("document_id", "chunk_id", "title", "version", "effective_date", "source_path")} for hit in hits]
        return {
            "answer": "依据当前生效的合成制度条款：\n" + "\n".join(excerpts),
            "blocked": False,
            "citations": citations,
            "contexts": [hit["text"] for hit in hits],
        }
