"""MCP 与 API 共用的工具服务：最小权限、血缘、审计和写操作确认门。"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from roles_config import get_user_permissions
from .ingestion import ROOT, get_document_lineage
from .multi_agent import Supervisor


class FinanceToolService:
    def __init__(self, root: Path = ROOT, output_dir: Path | None = None):
        self.root = root
        self.supervisor = Supervisor(root)
        self.output_dir = output_dir or root / "data" / "output"
        self.allowed_roles = {
            role.strip() for role in os.getenv("MCP_ALLOWED_ROLES", "employee,manager").split(",")
            if role.strip()
        }

    def _check_channel_role(self, role: str) -> None:
        if role not in self.allowed_roles:
            raise PermissionError(f"MCP 演示通道不允许角色 {role}；默认仅开放 employee,manager")

    def _audit(self, tool: str, role: str, status: str, record_id: str = "") -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(), "channel": "mcp",
            "tool": tool, "user_role": role, "status": status, "record_id": record_id,
            "payload_stored": False,
        }
        with (self.output_dir / "tool_audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def query_policy(self, question: str, role: str = "employee") -> dict[str, Any]:
        self._check_channel_role(role)
        result = self.supervisor.agents["policy"].run({"question": question}, role)
        self._audit("query_finance_policy", role, "blocked" if result["blocked"] else "completed")
        return result

    def check_payment(self, request_id: str, role: str = "employee") -> dict[str, Any]:
        self._check_channel_role(role)
        result = self.supervisor.agents["payment"].run({"request_id": request_id}, role)
        self._audit("check_payment_request", role, result["status"], request_id)
        return result

    def check_budget(self, request_id: str, role: str = "manager") -> dict[str, Any]:
        self._check_channel_role(role)
        result = self.supervisor.agents["budget"].run({"request_id": request_id}, role)
        self._audit("check_budget_request", role, result["status"], request_id)
        return result

    def analyze_reconciliation(self, role: str = "manager") -> dict[str, Any]:
        self._check_channel_role(role)
        result = self.supervisor.agents["reconciliation"].run({}, role)
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
        ticket_id = "TK-" + uuid.uuid4().hex[:12].upper()
        event = {
            "ticket_id": ticket_id, "scenario": scenario, "summary": summary,
            "created_by_role": role, "created_at": datetime.now(timezone.utc).isoformat(),
            "system": "local_synthetic_demo",
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with (self.output_dir / "tickets.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._audit("create_finance_ticket", role, "created", ticket_id)
        return {"status": "created", "created": True, "ticket_id": ticket_id, "system": "local_synthetic_demo"}
