"""付款、预算、对账确定性业务服务；统一读取 SQLite，金额不依赖大模型。"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from .database import build_database, connect, database_path
from .ingestion import ROOT

MONEY = Decimal("0.01")


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(MONEY)


def decimal_text(value: Decimal) -> str:
    return format(value.quantize(MONEY), ".2f")


def require_role(user_role: str, allowed: set[str]) -> None:
    if user_role not in allowed:
        raise PermissionError(f"角色 {user_role} 无权执行该场景；允许角色：{sorted(allowed)}")


class FinanceData:
    """统一业务数据访问层；原始文件只用于建库，运行时不再各读一套文件。"""

    def __init__(self, root: Path = ROOT, db_path: Path | None = None):
        self.root = root
        self.db_path = db_path or database_path(root)
        build_database(root, self.db_path)
        with connect(self.db_path) as conn:
            self.vendors = {x["vendor_id"]: dict(x) for x in conn.execute("SELECT * FROM vendors")}
            self.budgets = {x["project_id"]: dict(x) for x in conn.execute("SELECT * FROM budgets")}
            self.contracts = {x["contract_id"]: dict(x) for x in conn.execute("SELECT * FROM contracts")}
            self.payment_requests = {x["request_id"]: dict(x) for x in conn.execute("SELECT * FROM payment_requests")}
            self.invoices = {x["invoice_id"]: dict(x) for x in conn.execute("SELECT * FROM invoices")}
            self.acceptances = {x["acceptance_id"]: dict(x) for x in conn.execute("SELECT * FROM acceptances")}
            self.budget_requests = {x["request_id"]: dict(x) for x in conn.execute("SELECT * FROM budget_requests")}
            self.bank = [dict(x) for x in conn.execute("SELECT * FROM bank_transactions")]
            self.ledger = [dict(x) for x in conn.execute("SELECT * FROM ledger_entries")]
            milestone_rows = [dict(x) for x in conn.execute("SELECT * FROM contract_milestones")]
        for contract in self.contracts.values():
            contract["milestones"] = []
        for row in milestone_rows:
            row["requires"] = json.loads(row.pop("requires_json"))
            self.contracts[row["contract_id"]]["milestones"].append(row)

    def reload(self) -> None:
        """主数据或批次变化后重新读取 SQLite，避免长期使用启动时缓存。"""
        self.__init__(self.root, self.db_path)


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
        if not contract["approved"] or contract["status"] != "active":
            findings.append({"code": "contract_not_approved", "message": "合同未审批或已失效"})
        milestone = next((m for m in contract["milestones"] if m["code"] == request["milestone_code"]), None)
        if milestone is None:
            raise ValueError("付款里程碑不在合同约定中")
        expected = money(money(contract["total_amount"]) * Decimal(milestone["ratio"]))
        actual = money(request["amount"])
        if actual != expected:
            findings.append({
                "code": "milestone_amount_mismatch",
                "message": f"申请金额 {decimal_text(actual)} 元与里程碑应付 {decimal_text(expected)} 元不一致",
            })

        requires = set(milestone["requires"])
        if "invoice" in requires:
            invoice = self.data.invoices.get(request.get("invoice_id") or "")
            if invoice is None:
                findings.append({"code": "missing_invoice", "message": "缺少有效发票"})
            else:
                if money(invoice["amount"]) != actual:
                    findings.append({
                        "code": "invoice_amount_mismatch",
                        "message": f"发票金额 {decimal_text(money(invoice['amount']))} 元与申请金额 {decimal_text(actual)} 元不一致",
                    })
                if invoice["status"] != "valid":
                    findings.append({"code": "invalid_invoice", "message": f"发票状态为 {invoice['status']}，需人工复核"})
        if "acceptance" in requires:
            acceptance = self.data.acceptances.get(request.get("acceptance_id") or "")
            if acceptance is None:
                findings.append({"code": "missing_acceptance", "message": "缺少验收单"})
            elif acceptance["status"] != "approved":
                findings.append({"code": "acceptance_not_approved", "message": "验收单尚未审批通过"})
            elif acceptance["contract_id"] != contract["contract_id"] or acceptance["milestone_code"] != milestone["code"]:
                findings.append({"code": "acceptance_mismatch", "message": "验收单与合同或付款里程碑不匹配"})
        if "warranty_expired" in requires and not request["warranty_expired"]:
            findings.append({"code": "missing_warranty_expired", "message": "质保期尚未届满"})

        vendor = self.data.vendors.get(contract["vendor_id"])
        if not vendor or vendor["status"] != "active":
            findings.append({"code": "vendor_inactive", "message": "供应商主数据不存在或已停用"})
        elif request["account_fingerprint"] != vendor["account_fingerprint"]:
            findings.append({"code": "vendor_account_mismatch", "message": "收款账户与供应商主数据不一致"})
        if vendor and vendor["risk_level"] == "high":
            findings.append({"code": "high_risk_vendor", "message": "高风险供应商需增加合规复核"})

        approvers = ["部门负责人"]
        if actual > Decimal("50000"):
            approvers.append("财务负责人")
        if vendor and vendor["risk_level"] == "high":
            approvers.append("合规复核")
        if actual > expected and request["milestone_code"] == "advance":
            approvers.append("专项复核")
        return {
            "scenario": "payment", "request_id": request_id,
            "contract_id": contract["contract_id"], "project_id": contract["project_id"],
            "milestone": milestone["name"], "status": "pass" if not findings else "needs_review",
            "decision": "ready_for_approval" if not findings else "manual_review",
            "amount": decimal_text(actual), "expected_amount": decimal_text(expected),
            "currency": contract["currency"], "findings": findings, "approvers": approvers,
            "rule_version": "payment-policy:1.0",
            "evidence": ["POL-PAYMENT-POLICY-2026", "POL-SUPPLIER-MASTER-POLICY-2026",
                         contract["contract_id"], request_id],
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
        annual = money(budget["annual_budget"])
        committed = money(budget["committed"])
        actual = money(budget["actual"])
        frozen = money(budget["frozen"])
        available = annual - committed - actual - frozen
        requested = money(request["amount"])
        findings = []
        if requested > available:
            findings.append({
                "code": "budget_exceeded",
                "message": f"申请金额超出可用预算 {decimal_text(requested - available)} 元，应先调整预算或削减金额",
            })
        return {
            "scenario": "budget", "request_id": request_id, "project_id": request["project_id"],
            "department": budget["department"], "cost_center": budget["cost_center"],
            "cost_type": request["cost_type"], "reason": request["reason"],
            "status": "needs_review" if findings else "pass",
            "requested_amount": decimal_text(requested), "available_budget": decimal_text(available),
            "calculation": {
                "annual_budget": decimal_text(annual), "committed": decimal_text(committed),
                "actual": decimal_text(actual), "frozen": decimal_text(frozen),
                "formula": "annual_budget - committed - actual - frozen",
            },
            "findings": findings,
            "approvers": ["预算调整流程"] if findings else ["部门负责人", "财务BP"],
            "rule_version": "budget-policy:1.0",
            "evidence": ["POL-BUDGET-POLICY-2026", request["project_id"], request_id],
            "write_operation": False,
        }


class ReconciliationService:
    def __init__(self, data: FinanceData):
        self.data = data

    def analyze(self, user_role: str = "manager", account_id: str | None = None) -> dict[str, Any]:
        require_role(user_role, {"manager", "admin"})
        bank = [x for x in self.data.bank if not account_id or x["account_id"] == account_id]
        ledger = [x for x in self.data.ledger if not account_id or x["account_id"] == account_id]
        bank_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        ledger_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in bank:
            bank_groups[row["transaction_id"]].append(row)
        for row in ledger:
            ledger_groups[row["external_ref"]].append(row)

        findings: list[dict[str, Any]] = []
        matched = 0
        for txid in sorted(set(bank_groups) | set(ledger_groups)):
            bank_rows = bank_groups.get(txid, [])
            ledger_rows = ledger_groups.get(txid, [])
            if len(bank_rows) > 1:
                findings.append({"code": "duplicate_bank_record", "record_id": txid, "occurrences": len(bank_rows)})
                continue
            if len(ledger_rows) > 1:
                findings.append({"code": "duplicate_ledger_record", "record_id": txid, "occurrences": len(ledger_rows)})
                continue
            if bank_rows and not ledger_rows:
                findings.append({"code": "missing_in_ledger", "record_id": txid, "amount": decimal_text(money(bank_rows[0]["amount"]))})
                continue
            if ledger_rows and not bank_rows:
                findings.append({"code": "missing_in_bank", "record_id": txid, "voucher_id": ledger_rows[0]["voucher_id"], "ledger_amount": decimal_text(money(ledger_rows[0]["amount"]))})
                continue
            if not bank_rows or not ledger_rows:
                continue
            bank_row, ledger_row = bank_rows[0], ledger_rows[0]
            record_findings = []
            if money(bank_row["amount"]) != money(ledger_row["amount"]):
                record_findings.append({"code": "amount_mismatch", "record_id": txid, "bank_amount": decimal_text(money(bank_row["amount"])), "ledger_amount": decimal_text(money(ledger_row["amount"])), "voucher_id": ledger_row["voucher_id"]})
            if bank_row["currency"] != ledger_row["currency"]:
                record_findings.append({"code": "currency_mismatch", "record_id": txid, "bank_currency": bank_row["currency"], "ledger_currency": ledger_row["currency"]})
            if bank_row["transaction_date"] != ledger_row["posting_date"]:
                record_findings.append({"code": "date_mismatch", "record_id": txid, "bank_date": bank_row["transaction_date"], "ledger_date": ledger_row["posting_date"]})
            if bank_row["account_id"] != ledger_row["account_id"]:
                record_findings.append({"code": "account_mismatch", "record_id": txid, "bank_account": bank_row["account_id"], "ledger_account": ledger_row["account_id"]})
            if record_findings:
                findings.extend(record_findings)
            else:
                matched += 1

        counts = Counter(item["code"] for item in findings)
        return {
            "scenario": "reconciliation", "status": "pass" if not findings else "needs_review",
            "account_id": account_id or "all", "bank_record_count": len(bank),
            "ledger_record_count": len(ledger), "matched_count": matched,
            "difference_count": len(findings), "difference_record_count": len({x["record_id"] for x in findings}),
            "summary_by_type": dict(sorted(counts.items())), "findings": findings,
            "rule_version": "reconciliation-policy:1.0",
            "evidence": ["POL-RECONCILIATION-POLICY-2026", "bank_transactions", "ledger_entries"],
            "write_operation": False,
        }
