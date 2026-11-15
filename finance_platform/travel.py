"""原差旅报销能力整合为统一平台工具：标准查询、费用预检、确认后写入合成报销单。"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .database import FinanceRepository
from .scenarios import FinanceData, decimal_text, money, require_role


class TravelService:
    def __init__(self, data: FinanceData):
        self.data = data
        self.repository = FinanceRepository(data.root, data.db_path)

    def _city_tier(self, city: str) -> str:
        from .database import connect

        with connect(self.data.db_path) as conn:
            row = conn.execute("SELECT tier FROM dim_city_tier WHERE city=?", (city,)).fetchone()
        if not row:
            raise ValueError(f"标准库中未找到城市 {city} 的分级")
        return row["tier"]

    def _standard(self, expense_type: str, level: str, tier: str) -> Decimal:
        from .database import connect

        with connect(self.data.db_path) as conn:
            row = conn.execute(
                "SELECT limit_amount FROM dim_expense_standard WHERE expense_type=? AND level=? AND tier=?",
                (expense_type, level, tier),
            ).fetchone()
        if not row:
            raise ValueError(f"未找到费用标准：{expense_type}/{level}/{tier}")
        return money(row["limit_amount"])

    def precheck_from_text(self, question: str, user_role: str = "employee") -> dict[str, Any]:
        require_role(user_role, {"employee", "manager", "admin"})
        from .database import connect

        with connect(self.data.db_path) as conn:
            cities = [row["city"] for row in conn.execute("SELECT city FROM dim_city_tier ORDER BY LENGTH(city) DESC")]
        city = next((item for item in cities if item in question), None)
        if not city:
            raise ValueError("请提供出差城市，例如北京、深圳或成都")
        days_match = re.search(r"(\d{1,2})\s*天", question)
        if not days_match:
            raise ValueError("请提供出差天数，例如“出差3天”")
        days = int(days_match.group(1))
        if not 1 <= days <= 30:
            raise ValueError("演示报销天数须在 1—30 天之间")
        level = next((item for item in ("普通员工", "经理级", "总监级") if item in question), "普通员工")
        price_match = re.search(r"(?:住宿|酒店)(?:一晚|每晚|单价)?\s*(\d+(?:\.\d+)?)\s*元?", question)
        lodging_price = money(price_match.group(1)) if price_match else money(0)

        tier = self._city_tier(city)
        lodging_limit = self._standard("住宿", level, tier)
        meal_limit = self._standard("伙食", "通用", tier)
        transport_limit = self._standard("市内交通", "通用", "通用")
        nights = max(days - 1, 1)
        lodging_total = money(lodging_price * nights)
        meal_total = money(meal_limit * days)
        transport_total = money(transport_limit * days)
        total = money(lodging_total + meal_total + transport_total)
        overlimit_per_night = max(lodging_price - lodging_limit, money(0))
        overlimit = money(overlimit_per_night * nights)

        from .database import connect

        with connect(self.data.db_path) as conn:
            approvers = [row["node_name"] for row in conn.execute(
                "SELECT node_name FROM dim_approval_chain WHERE role=? ORDER BY node_order", (level,)
            )]
        if days >= 5:
            approvers.append("财务BP")
        findings = []
        if overlimit > 0:
            findings.append({
                "code": "lodging_overlimit",
                "message": f"住宿每晚超标 {decimal_text(overlimit_per_night)} 元，{nights} 晚合计超标 {decimal_text(overlimit)} 元；超支部分按制度转人工复核",
            })
        return {
            "scenario": "travel", "city": city, "city_tier": tier, "days": days, "nights": nights,
            "level": level, "lodging_price": decimal_text(lodging_price),
            "lodging_limit": decimal_text(lodging_limit), "lodging_total": decimal_text(lodging_total),
            "meal_total": decimal_text(meal_total), "transport_total": decimal_text(transport_total),
            "total_amount": decimal_text(total), "overlimit_amount": decimal_text(overlimit),
            "status": "needs_review" if findings else "pass", "findings": findings,
            "approvers": approvers, "rule_version": "travel-policy:2.1",
            "evidence": ["POL-TRAVEL-2026", "dim_expense_standard", "dim_approval_chain"],
            "write_operation": False,
        }
