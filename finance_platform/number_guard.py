from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

_NUMBER = re.compile(r"(?<![A-Za-z0-9])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")
_LIST_MARKER = re.compile(r"(?m)^\s*\d+[).\]]\s*")
_CITATION_MARKER = re.compile(r"\[\d+\]")
_CHINESE_PERCENT = re.compile(r"百分之([零一二三四五六七八九十百千万两]+)")
_CHINESE_NUMBER = re.compile(r"([零一二三四五六七八九十百千万两]+)(?=天|晚|元|个|笔|项|年|月|日)")
_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


@dataclass(frozen=True)
class NumericValidation:
    valid: bool
    missing: tuple[str, ...]
    checked: tuple[str, ...]


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "").rstrip("%")).normalize()
    except InvalidOperation:
        return None


def _chinese_decimal(text: str) -> Decimal | None:
    total, current = 0, 0
    for char in text:
        if char in _DIGITS:
            current = _DIGITS[char]
        elif char in _UNITS:
            unit = _UNITS[char]
            if unit == 10000:
                total = (total + (current or 1)) * unit
                current = 0
            else:
                total += (current or 1) * unit
                current = 0
        else:
            return None
    return Decimal(total + current).normalize()


def _numbers(text: str) -> set[Decimal]:
    cleaned = _CITATION_MARKER.sub("", _LIST_MARKER.sub("", text))
    values = {_decimal(match.group(0)) for match in _NUMBER.finditer(cleaned)}
    for match in _CHINESE_PERCENT.finditer(cleaned):
        values.add(_chinese_decimal(match.group(1)))
    for match in _CHINESE_NUMBER.finditer(cleaned):
        values.add(_chinese_decimal(match.group(1)))
    return {value for value in values if value is not None}


def _evidence_text(evidence: list[Any]) -> str:
    return "\n".join(item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, default=str)
                     for item in evidence)


def _derivable(values: set[Decimal]) -> set[Decimal]:
    """由证据数字可简单推导的值：均值、两两和/差（LLM 常做这类心算）。"""
    vals = list(values)
    derived: set[Decimal] = set()
    if vals:
        derived.add(sum(vals) / Decimal(len(vals)))
    for i, a in enumerate(vals):
        for b in vals[i + 1:]:
            derived.add(a + b)
            derived.add(abs(a - b))
    return derived


def _close(a: Decimal, b: Decimal, tol: Decimal = Decimal("0.05")) -> bool:
    """容忍 2 位小数舍入差异。"""
    return abs(a - b) <= tol


def validate_answer_numbers(answer: str, evidence: list[Any],
                            context_texts: list[str] | None = None) -> NumericValidation:
    """校验终稿数字：必须可在证据/上下文数字中找到，或可由其简单推导。

    context_texts：原始问题与会话历史文本——其中的数字（用户给的阈值、
    前文已核验过的数字）视为可信来源，避免误伤跨轮引用。
    """
    texts = list(evidence)
    if context_texts:
        texts.extend(t for t in context_texts if t)
    answer_numbers = _numbers(answer)
    base = _numbers(_evidence_text(texts))
    # 推导集只允许来自"语义文本"（工具文字输出、问题、历史）——
    # viz/SVG JSON 里的几何坐标噪声会让两两和凑出任意数字，必须排除
    semantic_numbers = _numbers("\n".join(t for t in texts if isinstance(t, str)))
    missing = answer_numbers - base
    if missing:
        derived = _derivable(semantic_numbers)
        # 计数型数字（"3 个部门/2 项建议"）：分析答案中必然出现，
        # 上限与证据数据量挂钩（999 这类凭空大数仍会被拦）
        count_cap = Decimal(max(8, len(semantic_numbers) * 2))
        missing = {m for m in missing
                   if not ((m == m.to_integral_value()) and Decimal(1) <= m <= count_cap)
                   and not any(_close(m, d) or (d != 0 and _close(m, d, abs(d) * Decimal("0.01")))
                               for d in derived)}
    return NumericValidation(not missing, tuple(str(value) for value in sorted(missing, key=lambda v: str(v))),
                             tuple(sorted((str(value) for value in answer_numbers))))
