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


def validate_answer_numbers(answer: str, evidence: list[Any]) -> NumericValidation:
    answer_numbers = _numbers(answer)
    evidence_numbers = _numbers(_evidence_text(evidence))
    missing = sorted(answer_numbers - evidence_numbers, key=lambda value: str(value))
    return NumericValidation(not missing, tuple(str(value) for value in missing),
                             tuple(sorted((str(value) for value in answer_numbers))))
