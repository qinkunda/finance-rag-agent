"""统一财务事项闭环：确定性规则、事务、权限、幂等、预算和审计。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from .database import build_database, connect, database_path
from .enterprise_database import (
    EnterpriseRepository, add_event, add_task, assignment_for, close_tasks,
    create_work_item, utc_now, workflow_steps,
)
from .ingestion import ROOT
from .scenarios import FinanceData, PaymentService, ReconciliationService, decimal_text, money
from .travel import TravelService


class WorkflowConflictError(ValueError):
    """乐观版本或幂等请求冲突。"""


class WorkflowService:
    def __init__(self, root: Path = ROOT, db_path: Path | None = None):
        self.root = root
        self.db_path = db_path or database_path(root)
        build_database(root, self.db_path)
        self.repository = EnterpriseRepository(self.db_path)

    @staticmethod
    def _hash(item_id: str, actor_id: str, action: str, payload: dict[str, Any]) -> str:
        content = json.dumps([item_id, actor_id, action, payload], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()

    @staticmethod
    def _item(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM work_items WHERE item_id=?", (item_id,)).fetchone()
        if not row:
            raise KeyError("事项不存在")
        return row

    @staticmethod
    def _audit(conn: sqlite3.Connection, actor: dict[str, Any], action: str, item: sqlite3.Row) -> None:
        conn.execute(
            "INSERT INTO audit_events(timestamp,channel,tool,user_role,status,record_id,payload_stored) VALUES (?,?,?,?,?,?,0)",
            (utc_now(), "enterprise_workspace", action, "|".join(actor["roles"]), "success", item["record_id"]),
        )

    @staticmethod
    def _require_actor(actor: dict[str, Any], item: sqlite3.Row) -> None:
        if item["scenario"] != "reconciliation" and item["created_by"] == actor["user_id"]:
            raise PermissionError("申请人与审批／处理人必须职责分离")
        if item["current_assignee"] != actor["user_id"] or item["current_role"] not in actor["roles"]:
            raise PermissionError("当前用户不是该节点的有效处理人")

    @staticmethod
    def _reserve_budget(conn: sqlite3.Connection, item: sqlite3.Row, at: str) -> dict[str, Any]:
        existing = conn.execute("SELECT * FROM budget_reservations WHERE item_id=?", (item["item_id"],)).fetchone()
        if existing and existing["status"] == "reserved":
            return dict(existing)
        if existing and existing["status"] == "consumed":
            raise WorkflowConflictError("预算预留已核销，不能重复使用")
        budget = conn.execute("SELECT * FROM budgets WHERE project_id=?", (item["project_id"],)).fetchone()
        if not budget:
            raise ValueError("预算项目不存在")
        amount = money(item["amount"])
        available = money(budget["annual_budget"]) - money(budget["committed"]) - money(budget["actual"]) - money(budget["frozen"])
        if amount > available:
            raise ValueError(f"可用预算不足：可用 {decimal_text(available)} 元，申请 {decimal_text(amount)} 元")
        reservation_id = existing["reservation_id"] if existing else "RSV-" + uuid.uuid4().hex[:12].upper()
        if existing:
            conn.execute("UPDATE budget_reservations SET amount=?,status='reserved',updated_at=? WHERE item_id=?",
                         (decimal_text(amount), at, item["item_id"]))
        else:
            conn.execute(
                "INSERT INTO budget_reservations VALUES (?,?,?,?,?,?,?)",
                (reservation_id, item["item_id"], item["project_id"], decimal_text(amount), "reserved", at, at),
            )
        conn.execute("UPDATE budgets SET frozen=? WHERE project_id=?", (decimal_text(money(budget["frozen"]) + amount), item["project_id"]))
        return {"reservation_id": reservation_id, "amount": decimal_text(amount), "status": "reserved"}

    @staticmethod
    def _release_budget(conn: sqlite3.Connection, item_id: str, at: str) -> None:
        row = conn.execute("SELECT * FROM budget_reservations WHERE item_id=?", (item_id,)).fetchone()
        if not row or row["status"] != "reserved":
            return
        budget = conn.execute("SELECT frozen FROM budgets WHERE project_id=?", (row["project_id"],)).fetchone()
        remaining = money(budget["frozen"]) - money(row["amount"])
        if remaining < 0:
            raise WorkflowConflictError("预算冻结余额异常")
        conn.execute("UPDATE budgets SET frozen=? WHERE project_id=?", (decimal_text(remaining), row["project_id"]))
        conn.execute("UPDATE budget_reservations SET status='released',updated_at=? WHERE item_id=?", (at, item_id))

    @staticmethod
    def _consume_budget(conn: sqlite3.Connection, item: sqlite3.Row, at: str) -> None:
        row = conn.execute("SELECT * FROM budget_reservations WHERE item_id=?", (item["item_id"],)).fetchone()
        if not row or row["status"] != "reserved":
            raise WorkflowConflictError("不存在可核销的有效预算预留")
        budget = conn.execute("SELECT actual,frozen FROM budgets WHERE project_id=?", (item["project_id"],)).fetchone()
        actual = money(budget["actual"]) + money(row["amount"])
        frozen = money(budget["frozen"]) - money(row["amount"])
        if frozen < 0:
            raise WorkflowConflictError("预算冻结余额异常")
        conn.execute("UPDATE budgets SET actual=?,frozen=? WHERE project_id=?",
                     (decimal_text(actual), decimal_text(frozen), item["project_id"]))
        conn.execute("UPDATE budget_reservations SET status='consumed',updated_at=? WHERE item_id=?", (at, item["item_id"]))

    def _expense_calculation(self, actor: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        if not set(actor["roles"]) & {"employee", "department_manager"}:
            raise PermissionError("仅员工或部门负责人可提交报销")
        city = str(payload.get("city", "")).strip()
        days = int(payload.get("days", 0))
        price = money(payload.get("lodging_price", 0))
        question = f"{city}出差{days}天，住宿{price}元，{actor['level']}"
        calculation = TravelService(FinanceData(self.root, self.db_path)).precheck_from_text(question, "employee")
        return calculation, days

    def precheck_expense(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        actor = self.repository.identity(session_id)
        calculation, days = self._expense_calculation(actor, payload)
        with connect(self.db_path) as conn:
            assignee, role = assignment_for(conn, "pending_department", actor["user_id"])
            row = conn.execute("SELECT display_name FROM users WHERE user_id=?", (assignee,)).fetchone()
            steps = workflow_steps(conn, {
                "scenario": "expense", "status": "pending_department", "created_by": actor["user_id"],
                "requires_finance_manager": money(calculation["overlimit_amount"]) > 0 or days >= 5,
                "return_to_status": None,
            })
        return {**calculation, "write_operation": False, "workflow_steps": steps,
                "next_step": {"status": "pending_department", "assignee_id": assignee,
                              "assignee_name": row[0] if row else assignee, "role": role}}

    def create_expense(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        actor = self.repository.identity(session_id)
        calculation, days = self._expense_calculation(actor, payload)
        report_id = "BX-2026-" + uuid.uuid4().hex[:8].upper()
        item_id = "WI-" + report_id
        at = utc_now()
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO expense_reports(report_id,city,days,level,lodging_price,lodging_total,
                   meal_total,transport_total,total_amount,overlimit_amount,approval_chain,created_by_role,created_at,system)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (report_id, calculation["city"], days, actor["level"], calculation["lodging_price"], calculation["lodging_total"],
                 calculation["meal_total"], calculation["transport_total"], calculation["total_amount"],
                 calculation["overlimit_amount"], "|".join(calculation["approvers"]), actor["knowledge_role"], at,
                 "enterprise_synthetic_demo"),
            )
            create_work_item(
                conn, scenario="expense", record_id=report_id,
                title=f"{calculation['city']}差旅报销 {calculation['total_amount']} 元", status="pending_department",
                department_id=actor["department_id"], project_id=actor["project_id"], amount=calculation["total_amount"],
                created_by=actor["user_id"], policy_version=calculation["rule_version"],
                requires_finance_manager=money(calculation["overlimit_amount"]) > 0 or days >= 5,
                item_id=item_id, at=at,
            )
            for finding in calculation["findings"]:
                conn.execute(
                    "INSERT INTO exceptions(exception_id,item_id,code,message,status,created_at) VALUES (?,?,?,?,?,?)",
                    ("EX-" + uuid.uuid4().hex[:12].upper(), item_id, finding["code"], finding["message"], "open", at),
                )
            item = self._item(conn, item_id)
            self._audit(conn, actor, "submit_expense", item)
        return self.repository.get_work_item(item_id, actor)

    def submit_payment(self, session_id: str, request_id: str) -> dict[str, Any]:
        actor = self.repository.identity(session_id)
        data = FinanceData(self.root, self.db_path)
        if request_id not in data.payment_requests:
            raise KeyError("付款申请不存在")
        request = data.payment_requests[request_id]
        if request["requester"] != actor["user_id"]:
            raise PermissionError("只能提交本人作为申请方的付款单")
        result = PaymentService(data).check(request_id, "employee")
        contract = data.contracts[request["contract_id"]]
        item_id = "WI-" + request_id
        at = utc_now()
        status = "needs_supplement" if result["findings"] else "pending_department"
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM work_items WHERE scenario='payment' AND record_id=?", (request_id,)).fetchone():
                raise WorkflowConflictError("该付款申请已进入流程，不能重复提交")
            create_work_item(
                conn, scenario="payment", record_id=request_id, title=f"合同付款 {request_id}｜{result['amount']} 元",
                status=status, department_id=actor["department_id"], project_id=contract["project_id"],
                amount=result["amount"], created_by=actor["user_id"], policy_version=result["rule_version"],
                requires_finance_manager="财务负责人" in result["approvers"], item_id=item_id, at=at,
            )
            for finding in result["findings"]:
                conn.execute(
                    "INSERT INTO exceptions(exception_id,item_id,code,message,status,created_at) VALUES (?,?,?,?,?,?)",
                    ("EX-" + uuid.uuid4().hex[:12].upper(), item_id, finding["code"], finding["message"], "open", at),
                )
            item = self._item(conn, item_id)
            self._audit(conn, actor, "submit_payment", item)
        return self.repository.get_work_item(item_id, actor)

    def list_reconciliation_runs(self, session_id: str) -> list[dict[str, Any]]:
        actor = self.repository.identity(session_id)
        if not set(actor["roles"]) & {"accountant", "finance_manager", "auditor"}:
            raise PermissionError("当前角色无权查看对账批次")
        with connect(self.db_path) as conn:
            return [dict(row) for row in conn.execute(
                """SELECT r.*,u.display_name AS creator_name,
                   (SELECT COUNT(*) FROM work_items w WHERE w.scenario='reconciliation' AND w.record_id LIKE r.run_id || ':%') AS item_count,
                   (SELECT COUNT(*) FROM work_items w WHERE w.scenario='reconciliation' AND w.record_id LIKE r.run_id || ':%' AND w.status='closed') AS closed_count
                   FROM reconciliation_runs r JOIN users u ON u.user_id=r.created_by
                   ORDER BY r.created_at DESC LIMIT 20"""
            )]

    def run_reconciliation(self, session_id: str, account_id: str | None = None) -> dict[str, Any]:
        actor = self.repository.identity(session_id)
        if not set(actor["roles"]) & {"accountant", "finance_manager"}:
            raise PermissionError("仅会计或财务负责人可发起对账批次")
        result = ReconciliationService(FinanceData(self.root, self.db_path)).analyze("manager", account_id)
        run_id = "REC-" + uuid.uuid4().hex[:12].upper()
        at = utc_now()
        item_ids = []
        items = []
        labels = {
            "duplicate_bank_record": "银行流水重复", "duplicate_ledger_record": "总账记录重复",
            "missing_in_ledger": "银行有流水、总账无记录", "missing_in_bank": "总账有记录、银行无流水",
            "amount_mismatch": "银行与总账金额不一致", "currency_mismatch": "币种不一致",
            "date_mismatch": "日期不一致", "account_mismatch": "账户不一致",
        }
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO reconciliation_runs VALUES (?,?,?,?,?,?,?,?)",
                (run_id, account_id or "all", result["bank_record_count"], result["ledger_record_count"],
                 result["matched_count"], len(result["findings"]), actor["user_id"], at),
            )
            for finding in result["findings"]:
                record_id = f"{run_id}:{finding['record_id']}:{finding['code']}"
                item_id = "WI-" + hashlib.sha256(record_id.encode()).hexdigest()[:20].upper()
                amount = finding.get("amount") or finding.get("bank_amount") or finding.get("ledger_amount") or "0.00"
                create_work_item(
                    conn, scenario="reconciliation", record_id=record_id,
                    title=f"对账差异｜{finding['record_id']}｜{labels.get(finding['code'], finding['code'])}",
                    status="pending_accountant", department_id="D-FIN", project_id="FIN-2026", amount=str(amount),
                    created_by=actor["user_id"], policy_version="reconciliation-policy:1.0", item_id=item_id, at=at,
                )
                conn.execute(
                    "INSERT INTO exceptions(exception_id,item_id,code,message,status,created_at) VALUES (?,?,?,?,?,?)",
                    ("EX-" + uuid.uuid4().hex[:12].upper(), item_id, finding["code"],
                     labels.get(finding["code"], finding["code"]), "open", at),
                )
                item_ids.append(item_id)
                items.append({"item_id": item_id, "record_id": finding["record_id"],
                              "code": finding["code"], "message": labels.get(finding["code"], finding["code"])})
            conn.execute(
                "INSERT INTO audit_events(timestamp,channel,tool,user_role,status,record_id,payload_stored) VALUES (?,?,?,?,?,?,0)",
                (at, "enterprise_workspace", "run_reconciliation", "|".join(actor["roles"]), "success", run_id),
            )
        return {"run_id": run_id, "item_ids": item_ids, "items": items, "analysis": result}

    def allowed_actions(self, identity: dict[str, Any], item: dict[str, Any]) -> list[str]:
        status = item["status"]
        own = item["created_by"] == identity["user_id"]
        current = (item["current_assignee"] == identity["user_id"] and item["current_role"] in identity["roles"]
                   and (item["scenario"] == "reconciliation" or not own))
        actions = []
        if own and status == "needs_supplement":
            actions.append("supplement")
        if own and status not in {"closed", "cancelled", "pending_accountant"} and not item.get("payments"):
            actions.append("cancel")
        if current:
            if status in {"pending_department", "pending_finance_review", "pending_finance_manager",
                          "pending_budget", "pending_budget_manager", "pending_verification"}:
                actions.extend(["approve", "return"])
            if status == "pending_payment":
                actions.extend(["pay", "return"])
            if status in {"pending_accountant", "paid"}:
                if item["scenario"] == "reconciliation":
                    actions.extend(["resolve", "return"])
                else:
                    actions.append("post")
        return actions

    def act(self, session_id: str, item_id: str, action: str, *, expected_version: int,
            idempotency_key: str, comment: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        actor = self.repository.identity(session_id)
        payload = payload or {}
        request_hash = self._hash(item_id, actor["user_id"], action, {"comment": comment, **payload})
        at = utc_now()
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT * FROM action_requests WHERE action_key=?", (idempotency_key,)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise WorkflowConflictError("幂等键已用于不同请求")
                replay = json.loads(previous["response_json"])
                replay["idempotent_replay"] = True
                return replay
            item = self._item(conn, item_id)
            if item["version"] != expected_version:
                raise WorkflowConflictError(f"事项已更新，当前版本为 {item['version']}，请刷新后重试")
            old_status = item["status"]
            new_status = old_status
            budget_changed = False

            if action == "supplement":
                if old_status != "needs_supplement" or item["created_by"] != actor["user_id"]:
                    raise PermissionError("仅申请人可在退补状态补充材料")
                reference = str(payload.get("reference", "")).strip()
                if not reference:
                    raise ValueError("补件必须填写材料编号或说明")
                conn.execute(
                    "INSERT INTO supplements VALUES (?,?,?,?,?,?,?)",
                    ("SUP-" + uuid.uuid4().hex[:12].upper(), item_id, str(payload.get("material_type", "补充材料")),
                     reference, comment, actor["user_id"], at),
                )
                conn.execute(
                    "UPDATE exceptions SET status='resolved',resolved_by=?,resolved_at=?,resolution_note=? WHERE item_id=? AND status!='resolved'",
                    (actor["user_id"], at, "已提交补充材料：" + reference, item_id),
                )
                new_status = item["return_to_status"] or "pending_finance_review"
                if new_status == "pending_budget":
                    self._reserve_budget(conn, item, at)
                    budget_changed = True
            elif action == "cancel":
                if item["created_by"] != actor["user_id"] or old_status in {"closed", "cancelled", "pending_accountant"}:
                    raise PermissionError("当前状态不允许申请人撤销")
                if conn.execute("SELECT 1 FROM payment_records WHERE item_id=?", (item_id,)).fetchone():
                    raise WorkflowConflictError("已支付事项不能撤销")
                self._release_budget(conn, item_id, at)
                new_status = "cancelled"
            elif action == "approve":
                self._require_actor(actor, item)
                if old_status == "pending_department":
                    new_status = "pending_finance_review"
                elif old_status == "pending_finance_review":
                    new_status = "pending_finance_manager" if item["requires_finance_manager"] else "pending_budget"
                elif old_status == "pending_finance_manager":
                    new_status = "pending_budget"
                elif old_status == "pending_budget":
                    self._reserve_budget(conn, item, at)
                    new_status = "pending_payment"
                    budget_changed = True
                elif old_status == "pending_budget_manager":
                    new_status = "pending_payment"
                elif old_status == "pending_verification" and item["scenario"] == "reconciliation":
                    new_status = "closed"
                    conn.execute(
                        "UPDATE exceptions SET status='resolved',resolved_by=?,resolved_at=?,resolution_note=? WHERE item_id=?",
                        (actor["user_id"], at, comment or "差异处理已复核关闭", item_id),
                    )
                else:
                    raise WorkflowConflictError("当前状态不支持审批通过")
                if new_status == "pending_budget":
                    self._reserve_budget(conn, item, at)
                    budget_changed = True
            elif action == "return":
                self._require_actor(actor, item)
                if old_status not in {"pending_department", "pending_finance_review", "pending_finance_manager",
                                      "pending_budget", "pending_budget_manager", "pending_payment", "pending_verification"}:
                    raise WorkflowConflictError("当前状态不支持退回")
                if not comment.strip():
                    raise ValueError("退回必须填写原因")
                self._release_budget(conn, item_id, at)
                new_status = "pending_accountant" if item["scenario"] == "reconciliation" else "needs_supplement"
                if item["scenario"] != "reconciliation":
                    conn.execute("UPDATE work_items SET return_to_status=? WHERE item_id=?", (old_status, item_id))
                conn.execute(
                    "INSERT INTO exceptions(exception_id,item_id,code,message,status,created_at) VALUES (?,?,?,?,?,?)",
                    ("EX-" + uuid.uuid4().hex[:12].upper(), item_id, "returned_for_supplement", comment, "open", at),
                )
            elif action == "pay":
                self._require_actor(actor, item)
                if old_status != "pending_payment" or item["scenario"] not in {"expense", "payment"}:
                    raise WorkflowConflictError("当前事项不在待支付节点")
                if conn.execute("SELECT 1 FROM payment_records WHERE item_id=?", (item_id,)).fetchone():
                    raise WorkflowConflictError("该事项已存在支付记录")
                self._reserve_budget(conn, item, at)
                self._consume_budget(conn, item, at)
                account = "EXPENSE-CLEARING"
                if item["scenario"] == "payment":
                    row = conn.execute("SELECT account_fingerprint FROM payment_requests WHERE request_id=?", (item["record_id"],)).fetchone()
                    account = row["account_fingerprint"]
                payment_id = "PAY-" + uuid.uuid4().hex[:12].upper()
                conn.execute(
                    "INSERT INTO payment_records VALUES (?,?,?,?,?,?,?,?)",
                    (payment_id, item_id, item["record_id"], item["amount"], account,
                     "SIM-" + hashlib.sha256(payment_id.encode()).hexdigest()[:16].upper(), "simulated_success", at),
                )
                new_status = "pending_accountant"
            elif action == "post":
                self._require_actor(actor, item)
                if old_status not in {"pending_accountant", "paid"} or item["scenario"] not in {"expense", "payment"}:
                    raise WorkflowConflictError("当前事项不在待入账节点")
                if old_status == "paid" and not conn.execute("SELECT 1 FROM payment_records WHERE item_id=?", (item_id,)).fetchone():
                    payment_id = "PAY-HIST-" + uuid.uuid4().hex[:8].upper()
                    conn.execute("INSERT INTO payment_records VALUES (?,?,?,?,?,?,?,?)",
                                 (payment_id, item_id, item["record_id"], item["amount"], "HISTORICAL-SEED",
                                  "SIM-HIST-" + item["record_id"], "simulated_success", at))
                if not conn.execute("SELECT 1 FROM payment_records WHERE item_id=?", (item_id,)).fetchone():
                    raise WorkflowConflictError("无支付记录，不能入账")
                if conn.execute("SELECT 1 FROM accounting_vouchers WHERE item_id=?", (item_id,)).fetchone():
                    raise WorkflowConflictError("该事项已入账")
                conn.execute(
                    "INSERT INTO accounting_vouchers VALUES (?,?,?,?,?,?)",
                    ("VCH-" + uuid.uuid4().hex[:12].upper(), item_id, item["record_id"], item["amount"], "posted", at),
                )
                new_status = "closed"
            elif action == "resolve":
                self._require_actor(actor, item)
                if old_status != "pending_accountant" or item["scenario"] != "reconciliation":
                    raise WorkflowConflictError("仅对账差异可提交处理结果")
                if not comment.strip():
                    raise ValueError("差异处理必须填写处理说明")
                conn.execute(
                    "UPDATE exceptions SET status='pending_verification',resolved_by=?,resolved_at=?,resolution_note=? WHERE item_id=?",
                    (actor["user_id"], at, comment, item_id),
                )
                new_status = "pending_verification"
            else:
                raise ValueError("不支持的流程动作")

            assignee, role = assignment_for(conn, new_status, item["created_by"])
            close_tasks(conn, item_id, at)
            conn.execute(
                """UPDATE work_items SET status=?,current_assignee=?,current_role=?,return_to_status=?,
                   version=version+1,updated_at=? WHERE item_id=? AND version=?""",
                (new_status, assignee, role,
                 None if action == "supplement" else (old_status if action == "return" and item["scenario"] != "reconciliation" else item["return_to_status"]),
                 at, item_id, expected_version),
            )
            if conn.total_changes < 1:
                raise WorkflowConflictError("事项版本冲突")
            if assignee and role and new_status != "closed":
                add_task(conn, item_id, assignee, role, item["title"], at)
            add_event(conn, item_id, actor["user_id"], action, old_status, new_status, comment, at)
            self._audit(conn, actor, action, item)
            assignee_row = conn.execute("SELECT display_name FROM users WHERE user_id=?", (assignee,)).fetchone() if assignee else None
            response = {"item_id": item_id, "record_id": item["record_id"], "action": action,
                        "from_status": old_status, "status": new_status, "version": expected_version + 1,
                        "current_assignee": assignee, "current_role": role,
                        "assignee_name": assignee_row[0] if assignee_row else None,
                        "idempotent_replay": False, "budget_changed": budget_changed or action in {"cancel", "return", "pay"}}
            conn.execute(
                "INSERT INTO action_requests VALUES (?,?,?,?,?,?,?)",
                (idempotency_key, item_id, actor["user_id"], action, request_hash, json.dumps(response, ensure_ascii=False), at),
            )
        return response
