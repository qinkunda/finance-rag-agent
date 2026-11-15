"""MCP 与统一平台共用业务库、知识检索、权限、审计和写操作确认门。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from roles_config import get_user_permissions
from .ingestion import ROOT, get_document_lineage
from .orchestrator import UnifiedFinancePlatform


class FinanceToolService:
    def __init__(self, root: Path = ROOT, output_dir: Path | None = None, db_path: Path | None = None):
        self.root = root
        self.platform = UnifiedFinancePlatform(root, db_path)
        self.output_dir = output_dir  # 保留旧参数兼容；业务写入统一进入 SQLite。
        self.allowed_roles = {
            role.strip() for role in os.getenv("MCP_ALLOWED_ROLES", "employee,manager").split(",")
            if role.strip()
        }

    def _check_channel_role(self, role: str) -> None:
        if role not in self.allowed_roles:
            raise PermissionError(f"MCP 演示通道不允许角色 {role}；默认仅开放 employee,manager")

    def _audit(self, tool: str, role: str, status: str, record_id: str = "") -> None:
        self.platform.repository.audit("mcp", tool, role, status, record_id)

    def query_policy(self, question: str, role: str = "employee") -> dict[str, Any]:
        self._check_channel_role(role)
        result = self.platform.policy.answer(question, role)
        self._audit("query_finance_policy", role, "blocked" if result["blocked"] else "completed")
        return result

    def check_payment(self, request_id: str, role: str = "employee") -> dict[str, Any]:
        self._check_channel_role(role)
        result = self.platform.payment.check(request_id, role)
        self._audit("check_payment_request", role, result["status"], request_id)
        return result

    def check_budget(self, request_id: str, role: str = "manager") -> dict[str, Any]:
        self._check_channel_role(role)
        result = self.platform.budget.check(request_id, role)
        self._audit("check_budget_request", role, result["status"], request_id)
        return result

    def analyze_reconciliation(self, role: str = "manager") -> dict[str, Any]:
        self._check_channel_role(role)
        result = self.platform.reconciliation.analyze(role)
        self._audit("analyze_reconciliation", role, result["status"])
        return result

    def lineage(self, document_id: str, role: str = "employee") -> dict[str, Any]:
        self._check_channel_role(role)
        result = get_document_lineage(document_id, self.root)
        if result["access_level"] not in get_user_permissions(role):
            raise PermissionError("当前角色无权查看该文档血缘")
        self._audit("get_document_lineage", role, "completed", document_id)
        return result

    def create_ticket(self, scenario: str, summary: str, confirmed: bool = False, role: str = "employee") -> dict[str, Any]:
        self._check_channel_role(role)
        if scenario not in {"travel", "payment", "budget", "reconciliation"}:
            raise ValueError("未知工单场景")
        summary = summary.strip()
        if not summary or len(summary) > 500:
            raise ValueError("summary 长度必须为 1—500 字")
        if confirmed is not True:
            return {
                "status": "confirmation_required", "created": False,
                "message": "创建工单是写操作；请核对摘要后显式设置 confirmed=true。",
                "write_operation": True,
            }
        result = self.platform.repository.create_ticket(scenario, summary, role)
        self._audit("create_finance_ticket", role, "created", result["ticket_id"])
        return result
