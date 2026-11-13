"""Supervisor + 四个专业 Agent 的可离线复现最小实现。

路由和规则执行是确定性的；后续可在不改变工具契约的情况下替换为 LLM 意图路由。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .ingestion import ROOT
from .policy_search import PolicySearch
from .scenarios import BudgetService, FinanceData, PaymentService, ReconciliationService

FINANCE_TERMS = ("财务", "报销", "差旅", "住宿", "付款", "合同", "预算", "对账", "发票", "税率", "工单", "质保金", "验收单")
REQUEST_ID_RE = re.compile(r"(?<![A-Z0-9])PR-\d+(?![A-Z0-9])", re.I)
BUDGET_ID_RE = re.compile(r"(?<![A-Z0-9])BR-\d+(?![A-Z0-9])", re.I)


class PolicyAgent:
    name = "policy_agent"

    def __init__(self, service: PolicySearch):
        self.service = service

    def run(self, payload: dict[str, Any], role: str) -> dict[str, Any]:
        return self.service.answer(str(payload.get("question", "")), role)


class PaymentAgent:
    name = "payment_agent"

    def __init__(self, service: PaymentService):
        self.service = service

    def run(self, payload: dict[str, Any], role: str) -> dict[str, Any]:
        return self.service.check(str(payload["request_id"]), role)


class BudgetAgent:
    name = "budget_agent"

    def __init__(self, service: BudgetService):
        self.service = service

    def run(self, payload: dict[str, Any], role: str) -> dict[str, Any]:
        return self.service.check(str(payload["request_id"]), role)


class ReconciliationAgent:
    name = "reconciliation_agent"

    def __init__(self, service: ReconciliationService):
        self.service = service

    def run(self, payload: dict[str, Any], role: str) -> dict[str, Any]:
        return self.service.analyze(role)


def human_summary(data: dict[str, Any]) -> str:
    scenario = data.get("scenario")
    if scenario == "payment":
        base = f"付款申请 {data['request_id']}：预检{'通过' if data['status'] == 'pass' else '需人工复核'}。"
        base += f"申请 {data['amount']} 元，里程碑应付 {data['expected_amount']} 元。"
        if data["findings"]:
            base += "问题：" + "；".join(x["message"] for x in data["findings"]) + "。"
        base += "审批链：" + " → ".join(data["approvers"]) + "。"
        return base
    if scenario == "budget":
        base = f"预算申请 {data['request_id']}：可用预算 {data['available_budget']} 元，申请 {data['requested_amount']} 元。"
        base += "预检" + ("需人工复核。" if data["findings"] else "通过。")
        if data["findings"]:
            base += data["findings"][0]["message"] + "。"
        return base
    if scenario == "reconciliation":
        base = f"对账完成：匹配 {data['matched_count']} 笔，发现 {data['difference_count']} 项差异。"
        names = {
            "amount_mismatch": "金额差异", "missing_in_ledger": "总账缺失",
            "missing_in_bank": "银行未达", "duplicate_bank_record": "重复流水",
        }
        return base + "、".join(names.get(x["code"], x["code"]) for x in data["findings"]) + "。"
    return str(data)


class Supervisor:
    def __init__(self, root: Path = ROOT):
        data = FinanceData(root)
        self.agents = {
            "policy": PolicyAgent(PolicySearch(root)),
            "payment": PaymentAgent(PaymentService(data)),
            "budget": BudgetAgent(BudgetService(data)),
            "reconciliation": ReconciliationAgent(ReconciliationService(data)),
        }

    @staticmethod
    def route(question: str) -> str:
        if REQUEST_ID_RE.search(question):
            return "payment"
        if BUDGET_ID_RE.search(question):
            return "budget"
        if "对账" in question and any(word in question for word in ("分析", "执行", "差异明细", "跑一下")):
            return "reconciliation"
        if "付款" in question and any(word in question for word in ("预检", "审核", "检查", "办理")):
            return "payment"
        if "预算" in question and any(word in question for word in ("预检", "审核", "检查", "申请明细")):
            return "budget"
        if any(term in question for term in FINANCE_TERMS):
            return "policy"
        return "out_of_scope"

    def execute(self, scenario: str, payload: dict[str, Any], role: str) -> dict[str, Any]:
        if scenario not in self.agents:
            raise ValueError(f"未知场景：{scenario}")
        trace = [{"step": "supervisor_route", "scenario": scenario}]
        data = self.agents[scenario].run(payload, role)
        trace.append({"step": "agent_completed", "agent": self.agents[scenario].name, "status": data.get("status", "completed")})
        if scenario == "policy":
            return {
                "scenario": scenario, "status": "blocked" if data["blocked"] else "completed",
                "answer": data["answer"], "data": data, "trace": trace,
            }
        return {
            "scenario": scenario, "status": data["status"], "answer": human_summary(data),
            "data": data, "trace": trace, "human_confirmation_required": False,
        }

    def handle_text(self, question: str, role: str = "employee") -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise ValueError("question 不能为空")
        scenario = self.route(question)
        if scenario == "out_of_scope":
            return {
                "scenario": scenario, "status": "blocked",
                "answer": "我是企业财务演示助手，仅处理制度、报销、付款、预算和对账问题。",
                "data": {}, "trace": [{"step": "supervisor_route", "scenario": scenario}],
            }
        if scenario == "policy":
            return self.execute(scenario, {"question": question}, role)
        if scenario == "payment":
            match = REQUEST_ID_RE.search(question)
            if not match:
                return self._needs_id(scenario, "请提供付款申请编号，例如 PR-001。")
            return self.execute(scenario, {"request_id": match.group(0).upper()}, role)
        if scenario == "budget":
            match = BUDGET_ID_RE.search(question)
            if not match:
                return self._needs_id(scenario, "请提供预算申请编号，例如 BR-001；该场景需要 manager 角色。")
            return self.execute(scenario, {"request_id": match.group(0).upper()}, role)
        return self.execute(scenario, {}, role)

    @staticmethod
    def _needs_id(scenario: str, message: str) -> dict[str, Any]:
        return {"scenario": scenario, "status": "needs_input", "answer": message, "data": {}, "trace": [{"step": "parameter_check", "scenario": scenario}]}
