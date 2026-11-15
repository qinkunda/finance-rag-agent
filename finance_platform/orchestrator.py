"""统一财务平台编排：一个会话入口、共用知识与业务工具、受控写操作。"""
from __future__ import annotations

import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from .database import FinanceRepository
from .ingestion import ROOT
from .policy_search import PolicySearch
from .scenarios import BudgetService, FinanceData, PaymentService, ReconciliationService
from .travel import TravelService

REQUEST_ID_RE = re.compile(r"(?<![A-Z0-9])PR-\d+(?![A-Z0-9])", re.I)
BUDGET_ID_RE = re.compile(r"(?<![A-Z0-9])BR-\d+(?![A-Z0-9])", re.I)
ACCOUNT_RE = re.compile(r"CNY-OPER-\d{3}", re.I)
FINANCE_TERMS = (
    "财务", "报销", "差旅", "住宿", "付款", "合同", "预算", "对账", "发票",
    "税率", "工单", "质保金", "验收单", "供应商", "成本", "费用",
)
SENSITIVE_TERMS = ("个人明细", "报销单据", "工资", "薪资", "他人报销")
ACTION_TERMS = ("提交", "办理", "创建", "生成工单", "发起工单", "建单", "帮我报销")
CONFIRM_TERMS = {"确认", "确认创建", "确认提交", "同意", "同意创建", "同意提交"}
CANCEL_TERMS = {"取消", "取消创建", "取消提交", "不用了", "作废"}


class UnifiedFinancePlatform:
    """确定性业务路由与工具执行；LLM 只负责制度生成，不负责金额或权限。"""

    def __init__(self, root: Path = ROOT, db_path: Path | None = None):
        self.root = root
        self.data = FinanceData(root, db_path)
        self.repository = FinanceRepository(root, self.data.db_path)
        self.policy = PolicySearch(root, self.data.db_path)
        self.payment = PaymentService(self.data)
        self.budget = BudgetService(self.data)
        self.reconciliation = ReconciliationService(self.data)
        self.travel = TravelService(self.data)
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def refresh(self) -> None:
        """主数据变更后刷新统一业务工具，保留同一知识目录和会话入口。"""
        self.data.reload()
        self.payment = PaymentService(self.data)
        self.budget = BudgetService(self.data)
        self.reconciliation = ReconciliationService(self.data)
        self.travel = TravelService(self.data)

    @staticmethod
    def route(question: str) -> str:
        if any(term in question for term in SENSITIVE_TERMS):
            return "policy"
        if REQUEST_ID_RE.search(question):
            return "payment"
        if BUDGET_ID_RE.search(question):
            return "budget"
        if "对账" in question:
            return "reconciliation"
        if "出差" in question or "帮我报销" in question or "提交报销" in question:
            return "travel"
        if any(term in question for term in FINANCE_TERMS):
            return "policy"
        return "out_of_scope"

    def _base_response(self, scenario: str, status: str, answer: str, data: dict[str, Any],
                       trace: list[dict[str, Any]], session_id: str) -> dict[str, Any]:
        return {
            "scenario": scenario, "status": status, "answer": answer, "data": data,
            "citations": [], "trace": trace, "session_id": session_id,
            "human_confirmation_required": False, "pending_action": None,
            "platform_mode": "offline" if os.getenv("FINANCE_OFFLINE_DEMO", "true").lower() == "true" else "online",
        }

    def _policy_evidence(self, scenario: str, role: str, trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        query = {
            "payment": "付款申请 合同 里程碑 发票 验收单 供应商账户",
            "budget": "可用预算 冻结金额 超出预算 调整流程",
            "reconciliation": "对账 金额差异 重复记录 币种 日期 总账缺失",
            "travel": "差旅 住宿标准 伙食 市内交通 审批链",
        }[scenario]
        hits = self.policy.search(query, role, top_k=3)
        trace.append({"step": "policy_retrieval", "mode": self.policy.mode, "hit_count": len(hits)})
        return self.policy._citations(hits)

    @staticmethod
    def _summary(data: dict[str, Any]) -> str:
        scenario = data["scenario"]
        if scenario == "payment":
            text = (
                f"付款申请 {data['request_id']}（合同 {data['contract_id']}）："
                f"预检{'通过' if data['status'] == 'pass' else '需人工复核'}。"
                f"申请 {data['amount']} 元，{data['milestone']}应付 {data['expected_amount']} 元。"
            )
            if data["findings"]:
                text += "问题：" + "；".join(x["message"] for x in data["findings"]) + "。"
            return text + "审批链：" + " → ".join(data["approvers"]) + "。"
        if scenario == "budget":
            text = (
                f"预算申请 {data['request_id']}：可用预算 {data['available_budget']} 元，"
                f"申请 {data['requested_amount']} 元。"
                f"预检{'通过' if data['status'] == 'pass' else '需人工复核'}。"
            )
            if data["findings"]:
                text += data["findings"][0]["message"] + "。"
            return text
        if scenario == "reconciliation":
            names = {
                "amount_mismatch": "金额差异", "missing_in_ledger": "总账缺失",
                "missing_in_bank": "银行未达", "duplicate_bank_record": "重复银行流水",
                "duplicate_ledger_record": "重复总账记录", "currency_mismatch": "币种差异",
                "date_mismatch": "日期差异", "account_mismatch": "账户差异",
            }
            text = (
                f"对账完成：匹配 {data['matched_count']} 笔，发现 {data['difference_count']} 项差异，"
                f"涉及 {data['difference_record_count']} 个记录标识。"
            )
            if data["findings"]:
                return text + "、".join(names.get(x["code"], x["code"]) for x in data["findings"]) + "。"
            return text
        if scenario == "travel":
            text = (
                f"{data['city']}出差 {data['days']} 天（{data['level']}）：费用试算合计 "
                f"{data['total_amount']} 元，其中住宿 {data['lodging_total']} 元、伙食 "
                f"{data['meal_total']} 元、市内交通 {data['transport_total']} 元。"
            )
            if data["findings"]:
                text += data["findings"][0]["message"] + "。"
            return text + "审批链：" + " → ".join(data["approvers"]) + "。"
        return str(data)

    def _prepare_action(self, response: dict[str, Any], data: dict[str, Any], role: str) -> dict[str, Any]:
        scenario = data["scenario"]
        record_id = data.get("request_id", "")
        action_id = "ACT-" + uuid.uuid4().hex[:12].upper()
        summary = self._summary(data)
        if scenario == "travel":
            action = {
                "action_id": action_id, "kind": "expense", "scenario": scenario,
                "record_id": record_id, "role": role, "summary": summary, "precheck": data,
            }
        else:
            action = {
                "action_id": action_id, "kind": "ticket", "scenario": scenario,
                "record_id": record_id, "role": role, "summary": summary,
                "ticket_status": "needs_review" if data["status"] == "needs_review" else "pending",
                "precheck": data,
            }
        response["status"] = "confirmation_required"
        response["answer"] = summary + "\n请核对后回复“确认”创建本地合成记录，或回复“取消”。此确认不会触发真实付款或企业审批。"
        response["human_confirmation_required"] = True
        response["pending_action"] = {
            "action_id": action_id, "scenario": scenario, "record_id": record_id, "summary": summary,
        }
        response["trace"].append({"step": "human_confirmation_gate", "write_executed": False})
        return action

    def _confirm(self, session_id: str, role: str) -> dict[str, Any]:
        with self._lock:
            action = self._pending.get(session_id)
        if not action:
            return self._base_response(
                "workflow", "no_pending_action", "当前会话没有待确认的写操作。", {},
                [{"step": "confirmation_check", "pending": False}], session_id,
            )
        if action["role"] != role:
            raise PermissionError("待确认动作属于原演示角色，不能切换角色后代为确认")
        if action["kind"] == "expense":
            result = self.repository.create_expense_report(action["precheck"], role)
            answer = f"合成报销单 {result['report_id']} 已写入统一 SQLite 业务库；未连接真实企业报销系统。"
            record_id = result["report_id"]
        else:
            result = self.repository.create_ticket(
                action["scenario"], action["summary"], role, action["record_id"],
                action["ticket_status"], action["precheck"],
            )
            answer = f"合成工单 {result['ticket_id']} 已写入统一 SQLite 业务库；未连接真实 OA、付款或记账系统。"
            record_id = result["ticket_id"]
        with self._lock:
            self._pending.pop(session_id, None)
        trace = [
            {"step": "human_confirmation_gate", "confirmed": True},
            {"step": "write_completed", "system": "local_synthetic_demo", "record_id": record_id},
        ]
        return self._base_response(action["scenario"], "created", answer, result, trace, session_id)

    def _cancel(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            action = self._pending.pop(session_id, None)
        answer = "已取消待确认动作，未写入工单或报销单。" if action else "当前会话没有可取消的待办动作。"
        return self._base_response(
            "workflow", "cancelled" if action else "no_pending_action", answer, {},
            [{"step": "write_cancelled", "write_executed": False}], session_id,
        )

    def handle(self, question: str, user_role: str = "employee", session_id: str = "default",
               legacy_writes: bool = True) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise ValueError("question 不能为空")
        if len(question) > 1000:
            raise ValueError("question 不能超过 1000 字")
        if not 1 <= len(session_id) <= 100:
            raise ValueError("session_id 长度必须为 1—100 字")
        if question in CANCEL_TERMS:
            return self._cancel(session_id)
        if question in CONFIRM_TERMS:
            return self._confirm(session_id, user_role)

        scenario = self.route(question)
        trace = [{"step": "unified_route", "scenario": scenario}]
        if scenario == "out_of_scope":
            response = self._base_response(
                scenario, "blocked", "我是统一财务演示助手，仅处理制度、报销、付款、预算和对账问题。",
                {}, trace, session_id,
            )
            self.repository.audit("platform", "unified_entry", user_role, "blocked")
            return response
        if scenario == "policy":
            result = self.policy.answer(question, user_role)
            trace.append({"step": "policy_retrieval", "mode": result["retrieval_mode"]})
            response = self._base_response(
                scenario, "blocked" if result["blocked"] else "completed", result["answer"], result,
                trace, session_id,
            )
            response["citations"] = result["citations"]
            self.repository.audit("platform", "policy_search", user_role, response["status"])
            return response

        if scenario == "payment":
            request_id = REQUEST_ID_RE.search(question).group(0).upper()
            data = self.payment.check(request_id, user_role)
        elif scenario == "budget":
            request_id = BUDGET_ID_RE.search(question).group(0).upper()
            data = self.budget.check(request_id, user_role)
        elif scenario == "reconciliation":
            account_match = ACCOUNT_RE.search(question)
            data = self.reconciliation.analyze(user_role, account_match.group(0).upper() if account_match else None)
        elif scenario == "travel":
            data = self.travel.precheck_from_text(question, user_role)
        else:
            raise ValueError(f"未知场景：{scenario}")

        trace.append({"step": "business_tool_completed", "tool": f"{scenario}_precheck", "status": data["status"]})
        response = self._base_response(scenario, data["status"], self._summary(data), data, trace, session_id)
        response["citations"] = self._policy_evidence(scenario, user_role, trace)
        record_id = data.get("request_id", "")
        self.repository.audit("platform", f"{scenario}_precheck", user_role, data["status"], record_id)
        if legacy_writes and any(term in question for term in ACTION_TERMS):
            action = self._prepare_action(response, data, user_role)
            with self._lock:
                if len(self._pending) >= 1000:
                    self._pending.pop(next(iter(self._pending)))
                self._pending[session_id] = action
        return response

    def handle_enterprise(self, question: str, session_id: str) -> dict[str, Any]:
        """企业会话入口：身份、角色、职级和单据数据范围均从 SQLite 派生。"""
        from .enterprise_database import EnterpriseRepository
        repository = EnterpriseRepository(self.data.db_path)
        identity = repository.identity(session_id)
        scenario = self.route(question.strip())
        if scenario == "travel":
            question = re.sub(r"普通员工|经理级|总监级", "", question) + f"，{identity['level']}"
        if scenario == "payment":
            match = REQUEST_ID_RE.search(question)
            if match:
                request_id = match.group(0).upper()
                request = self.data.payment_requests.get(request_id)
                finance_roles = {"finance_reviewer", "finance_bp", "finance_manager", "treasury", "accountant", "auditor"}
                if request and not set(identity["roles"]) & finance_roles:
                    allowed = request["requester"] == identity["user_id"]
                    if not allowed:
                        # 旧种子单的 requester 是合成占位值；统一事项中的申请人和
                        # 部门数据范围才是企业会话的权限真源。
                        from .database import connect
                        with connect(self.data.db_path) as conn:
                            item = conn.execute(
                                "SELECT * FROM work_items WHERE scenario='payment' AND record_id=?", (request_id,)
                            ).fetchone()
                        allowed = bool(item and repository.can_view(identity, dict(item)))
                    if not allowed:
                        raise PermissionError("只能查询本人发起或有财务权限的付款申请")
        return self.handle(question, identity["knowledge_role"], session_id, legacy_writes=False)

    def handle_text(self, question: str, role: str = "employee") -> dict[str, Any]:
        return self.handle(question, role, session_id="legacy-text")

    def execute(self, scenario: str, payload: dict[str, Any], role: str = "employee") -> dict[str, Any]:
        """兼容结构化调用；只执行查询和预检，写操作必须走统一会话确认。"""
        if scenario == "policy":
            return self.handle(str(payload.get("question", "")), role, "structured-policy")
        if scenario == "payment":
            question = f"请预检付款申请 {payload.get('request_id', '')}"
        elif scenario == "budget":
            question = f"检查预算申请 {payload.get('request_id', '')}"
        elif scenario == "reconciliation":
            question = "执行对账分析 " + str(payload.get("account_id", ""))
        elif scenario == "travel":
            question = str(payload.get("question", ""))
        else:
            raise ValueError(f"未知场景：{scenario}")
        return self.handle(question, role, "structured-" + scenario)
