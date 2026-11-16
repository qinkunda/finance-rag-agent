from __future__ import annotations

import os

_COMPLEX_TERMS = (
    "分析", "报告", "对比", "比较", "汇总", "统计", "预测", "风险", "原因", "优化",
    "多部门", "各部门", "全量", "趋势", "占用率", "执行率", "对账差异", "管理层",
    "同时", "并且", "追问", "第一轮", "第二轮", "综合",
)


def model_for_question(question: str) -> str:
    default_model = os.getenv("FINANCE_LLM_MODEL", "qwen-plus")
    if os.getenv("FINANCE_MODEL_ROUTING_ENABLED", "true").lower() != "true":
        return default_model
    small_model = os.getenv("FINANCE_LLM_SMALL_MODEL", "qwen-flash")
    text = question.strip()
    complex = len(text) > 100 or any(term in text for term in _COMPLEX_TERMS)
    return default_model if complex else small_model
