"""付款、预算、对账三个确定性场景；金额计算不依赖大模型。"""
from __future__ import annotations

import csv
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from .ingestion import ROOT

MONEY = Decimal("0.01")


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(MONEY)


def decimal_text(value: Decimal) -> str:
    return format(value.quantize(MONEY), ".2f")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def require_role(user_role: str, allowed: set[str]) -> None:
    if user_role not in allowed:
        raise PermissionError(f"角色 {user_role} 无权执行该场景；允许角色：{sorted(allowed)}")


class FinanceData:
    def __init__(self, root: Path = ROOT):
        self.root = root
        raw = root / "data" / "raw"
        self.contracts = {x["contract_id"]: x for x in load_json(raw / "contracts" / "contracts.json")}
        self.payment_requests = {x["request_id"]: x for x in load_json(raw / "transactions" / "payment_requests.json")}
        self.vendors = {x["vendor_id"]: x for x in load_json(raw / "vendors" / "vendors.json")}
        self.budgets = {x["project_id"]: x for x in load_csv(raw / "budget" / "budgets.csv")}
        self.budget_requests = {x["request_id"]: x for x in load_json(raw / "budget" / "budget_requests.json")}
        self.bank = load_csv(raw / "reconciliation" / "bank_statement.csv")
        self.ledger = load_csv(raw / "reconciliation" / "general_ledger.csv")


class PaymentService:
    def __init__(self, data: FinanceData):
        self.data = data

    def check(self, request_id: str, user_role: str = "employee") -> dict[str, Any]:
        require_role(user_role, {"employee", "manager", "admin"})
        try:
            request = self.data.payment_requests[request_id]
        except KeyError as exc:
            raise KeyError(f"未找到付款申请：{request_id}") from exc
        try:
            contract = self.data.contracts[request["contract_id"]]
        except KeyError as exc:
            raise ValueError("付款申请关联的合同不存在") from exc
        findings: list[dict[str, str]] = []
        if not contract.get("approved") or contract.get("status") != "active":
            findings.append({"code": "contract_not_approved", "message": "合同未审批或已失效"})
        milestone = next((m for m in contract["milestones"] if m["code"] == request["milestone"]), None)
        if milestone is None:
            raise ValueError("付款里程碑不在合同约定中")
        expected = money(money(contract["total_amount"]) * Decimal(milestone["ratio"]))
        actual = money(request["amount"])
        if actual != expected:
            findings.append({
                "code": "milestone_amount_mismatch",
                "message": f"申请金额 {decimal_text(actual)} 元与里程碑应付 {decimal_text(expected)} 元不一致",
            })
        requirements = {
            "invoice": (bool(request.get("invoice")), "缺少有效发票"),
            "acceptance": (bool(request.get("acceptance")), "缺少验收单"),
            "warranty_expired": (bool(request.get("warranty_expired")), "质保期尚未届满"),
        }
        for requirement in milestone["requires"]:
            if requirement == "contract_approved":
                continue
            passed, message = requirements[requirement]
            if not passed:
                findings.append({"code": f"missing_{requirement}", "message": message})
        if not request.get("account_matches_vendor"):
            findings.append({"code": "vendor_account_mismatch", "message": "收款账户与供应商主数据不一致"})
        vendor = self.data.vendors.get(contract["vendor_id"])
        if not vendor or vendor.get("status") != "active":
            findings.append({"code": "vendor_inactive", "message": "供应商主数据不存在或已停用"})
        approvers = ["部门负责人"] + (["财务负责人"] if actual > Decimal("50000") else [])
        if actual > expected and request["milestone"] == "advance":
            approvers.append("专项复核")
        return {
            "scenario": "payment",
            "request_id": request_id,
            "status": "pass" if not findings else "needs_review",
            "decision": "ready_for_approval" if not findings else "manual_review",
            "amount": decimal_text(actual),
            "expected_amount": decimal_text(expected),
            "currency": contract["currency"],
            "findings": findings,
            "approvers": approvers,
            "rule_version": "payment-policy:1.0",
            "evidence": ["POL-PAYMENT-POLICY-2026", contract["contract_id"], request_id],
            "write_operation": False,
        }


class BudgetService:
    def __init__(self, data: FinanceData):
        self.data = data

    def check(self, request_id: str, user_role: str = "manager") -> dict[str, Any]:
        require_role(user_role, {"manager", "admin"})
        try:
            request = self.data.budget_requests[request_id]
            budget = self.data.budgets[request["project_id"]]
        except KeyError as exc:
            raise KeyError(f"预算申请或项目不存在：{request_id}") from exc
        available = money(budget["annual_budget"]) - money(budget["committed"]) - money(budget["actual"])
        requested = money(request["amount"])
        exceeds = requested > available
        findings = []
        if exceeds:
            findings.append({
                "code": "budget_exceeded",
                "message": f"申请金额超出可用预算 {decimal_text(requested - available)} 元，应先调整预算或削减金额",
            })
        return {
            "scenario": "budget",
            "request_id": request_id,
            "project_id": request["project_id"],
            "status": "needs_review" if exceeds else "pass",
            "requested_amount": decimal_text(requested),
            "available_budget": decimal_text(available),
            "calculation": {
                "annual_budget": decimal_text(money(budget["annual_budget"])),
                "committed": decimal_text(money(budget["committed"])),
                "actual": decimal_text(money(budget["actual"])),
                "formula": "annual_budget - committed - actual",
            },
            "findings": findings,
            "approvers": ["预算调整流程"] if exceeds else ["部门负责人", "财务BP"],
            "rule_version": "budget-policy:1.0",
            "evidence": ["POL-BUDGET-POLICY-2026", request["project_id"], request_id],
            "write_operation": False,
        }


class ReconciliationService:
    def __init__(self, data: FinanceData):
        self.data = data

    def analyze(self, user_role: str = "manager") -> dict[str, Any]:
        require_role(user_role, {"manager", "admin"})
        bank = self.data.bank
        ledger = self.data.ledger
        bank_counts = Counter(row["transaction_id"] for row in bank)
        duplicate_ids = {key for key, count in bank_counts.items() if count > 1}
        findings: list[dict[str, Any]] = []
        matched = 0
        ledger_by_ref = {row["external_ref"]: row for row in ledger}
        unique_bank = {}
        for row in bank:
            txid = row["transaction_id"]
            if txid in duplicate_ids:
                continue
            unique_bank[txid] = row
            gl = ledger_by_ref.get(txid)
            if gl is None:
                findings.append({"code": "missing_in_ledger", "record_id": txid, "amount": decimal_text(money(row["amount"]))})
            elif money(gl["amount"]) != money(row["amount"]):
                findings.append({
                    "code": "amount_mismatch", "record_id": txid,
                    "bank_amount": decimal_text(money(row["amount"])),
                    "ledger_amount": decimal_text(money(gl["amount"])),
                    "voucher_id": gl["voucher_id"],
                })
            else:
                matched += 1
        for txid in sorted(duplicate_ids):
            rows = [row for row in bank if row["transaction_id"] == txid]
            findings.append({"code": "duplicate_bank_record", "record_id": txid, "occurrences": len(rows)})
        bank_ids = {row["transaction_id"] for row in bank}
        for gl in ledger:
            if gl["external_ref"] not in bank_ids:
                findings.append({
                    "code": "missing_in_bank", "record_id": gl["external_ref"],
                    "voucher_id": gl["voucher_id"], "ledger_amount": decimal_text(money(gl["amount"])),
                })
        return {
            "scenario": "reconciliation",
            "status": "pass" if not findings else "needs_review",
            "bank_record_count": len(bank),
            "ledger_record_count": len(ledger),
            "matched_count": matched,
            "difference_count": len(findings),
            "findings": findings,
            "rule_version": "reconciliation-policy:1.0",
            "evidence": ["POL-RECONCILIATION-POLICY-2026", "bank_statement.csv", "general_ledger.csv"],
            "write_operation": False,
        }
