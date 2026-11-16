"""组织、主数据和制度变更：当前状态即时生效，历史通过事件与版本保留。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .database import build_database, connect, database_path
from .enterprise_database import EnterpriseRepository, ROLE_ASSIGNEE, add_task, close_tasks, utc_now
from .ingestion import ROOT


class ChangeService:
    def __init__(self, root: Path = ROOT, db_path: Path | None = None):
        self.root = root
        self.db_path = db_path or database_path(root)
        build_database(root, self.db_path)
        self.repository = EnterpriseRepository(self.db_path)

    def _actor(self, session_id: str, allowed: set[str]) -> dict[str, Any]:
        actor = self.repository.identity(session_id)
        if not set(actor["roles"]) & allowed:
            raise PermissionError("当前角色无权执行主数据变更")
        return actor

    @staticmethod
    def _change(conn: sqlite3.Connection, entity_type: str, entity_id: str, change_type: str,
                before: Any, after: Any, actor_id: str, effective_at: str) -> None:
        conn.execute(
            "INSERT INTO change_events VALUES (?,?,?,?,?,?,?,?,?)",
            ("CHG-" + uuid.uuid4().hex[:12].upper(), entity_type, entity_id, change_type,
             json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False),
             effective_at, actor_id, utc_now()),
        )

    @staticmethod
    def _reassign(conn: sqlite3.Connection, user_id: str, replacement_id: str | None, at: str) -> int:
        rows = [dict(x) for x in conn.execute(
            "SELECT * FROM work_items WHERE current_assignee=? AND status NOT IN ('closed','cancelled')", (user_id,)
        )]
        for item in rows:
            assignee = replacement_id
            if not assignee:
                candidate = ROLE_ASSIGNEE.get(item["current_role"] or "")
                if candidate and candidate != user_id:
                    assignee = candidate
            if not assignee:
                row = conn.execute(
                    """SELECT u.user_id FROM users u JOIN user_roles ur ON ur.user_id=u.user_id
                       WHERE u.status='active' AND ur.role_code=? AND ur.is_current=1 AND u.user_id!=? LIMIT 1""",
                    (item["current_role"], user_id),
                ).fetchone()
                assignee = row["user_id"] if row else None
            if not assignee:
                raise ValueError(f"事项 {item['item_id']} 找不到接管人")
            target = conn.execute(
                """SELECT u.status FROM users u JOIN user_roles ur ON ur.user_id=u.user_id
                   WHERE u.user_id=? AND ur.role_code=? AND ur.is_current=1""", (assignee, item["current_role"])
            ).fetchone()
            if not target or target["status"] != "active":
                raise ValueError("接管人不存在、已停用或没有对应角色")
            close_tasks(conn, item["item_id"], at)
            conn.execute("UPDATE work_items SET current_assignee=?,version=version+1,updated_at=? WHERE item_id=?",
                         (assignee, at, item["item_id"]))
            add_task(conn, item["item_id"], assignee, item["current_role"], item["title"], at)
            conn.execute(
                "INSERT INTO workflow_events(item_id,actor_id,action,from_status,to_status,comment,created_at) VALUES (?,?,?,?,?,?,?)",
                (item["item_id"], user_id, "reassign", item["status"], item["status"], f"待办由 {user_id} 转交 {assignee}", at),
            )
        return len(rows)

    def deactivate_user(self, session_id: str, user_id: str, replacement_id: str | None = None) -> dict[str, Any]:
        actor = self._actor(session_id, {"admin"})
        at = utc_now()
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not row:
                raise KeyError("用户不存在")
            before = dict(row)
            count = self._reassign(conn, user_id, replacement_id, at)
            conn.execute("UPDATE users SET status='terminated',effective_to=? WHERE user_id=?", (at[:10], user_id))
            conn.execute("UPDATE sessions SET active=0 WHERE user_id=?", (user_id,))
            after = dict(conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone())
            self._change(conn, "user", user_id, "deactivate", before, after, actor["user_id"], at)
        return {"user_id": user_id, "status": "terminated", "reassigned_count": count, "replacement_id": replacement_id}

    def change_user(self, session_id: str, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        actor = self._actor(session_id, {"admin"})
        at = utc_now()
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not row:
                raise KeyError("用户不存在")
            before = dict(row)
            before["roles"] = [x["role_code"] for x in conn.execute(
                "SELECT role_code FROM user_roles WHERE user_id=? AND is_current=1", (user_id,)
            )]
            fields, values = [], []
            for key in ("department_id", "project_id", "manager_id", "position", "level"):
                if key in payload:
                    fields.append(key + "=?")
                    values.append(str(payload[key]))
            if fields:
                conn.execute(f"UPDATE users SET {','.join(fields)} WHERE user_id=?", (*values, user_id))
            if "roles" in payload:
                roles = sorted(set(payload["roles"]))
                if not roles:
                    raise ValueError("至少保留一个角色")
                valid = {x["role_code"] for x in conn.execute("SELECT role_code FROM roles")}
                if not set(roles) <= valid:
                    raise ValueError("包含未定义角色")
                conn.execute("UPDATE user_roles SET is_current=0,effective_to=? WHERE user_id=? AND is_current=1", (at, user_id))
                for role in roles:
                    conn.execute("INSERT INTO user_roles VALUES (?,?,?,?,1)", (user_id, role, at, None))
                self._reassign(conn, user_id, payload.get("replacement_id"), at)
            after = dict(conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone())
            after["roles"] = [x["role_code"] for x in conn.execute(
                "SELECT role_code FROM user_roles WHERE user_id=? AND is_current=1", (user_id,)
            )]
            self._change(conn, "user", user_id, "update", before, after, actor["user_id"], at)
        return after

    def change_vendor(self, session_id: str, vendor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        actor = self._actor(session_id, {"admin", "finance_manager"})
        allowed = {key: str(value) for key, value in payload.items() if key in {"status", "risk_level", "account_fingerprint"}}
        if not allowed:
            raise ValueError("没有可更新的供应商字段")
        at = utc_now()
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM vendors WHERE vendor_id=?", (vendor_id,)).fetchone()
            if not row:
                raise KeyError("供应商不存在")
            before = dict(row)
            conn.execute(f"UPDATE vendors SET {','.join(k + '=?' for k in allowed)} WHERE vendor_id=?",
                         (*allowed.values(), vendor_id))
            after = dict(conn.execute("SELECT * FROM vendors WHERE vendor_id=?", (vendor_id,)).fetchone())
            self._change(conn, "vendor", vendor_id, "update", before, after, actor["user_id"], at)
        return after

    def adjust_budget(self, session_id: str, project_id: str, annual_budget: str, reason: str) -> dict[str, Any]:
        actor = self._actor(session_id, {"finance_manager"})
        if not reason.strip():
            raise ValueError("预算调整必须填写原因")
        from .scenarios import decimal_text, money
        amount = money(annual_budget)
        if amount <= 0:
            raise ValueError("年度预算必须大于零")
        at = utc_now()
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM budgets WHERE project_id=?", (project_id,)).fetchone()
            if not row:
                raise KeyError("预算项目不存在")
            used = money(row["committed"]) + money(row["actual"]) + money(row["frozen"])
            if amount < used:
                raise ValueError("调整后的年度预算不能低于已承诺、已发生和已冻结金额之和")
            before = dict(row)
            conn.execute("UPDATE budgets SET annual_budget=? WHERE project_id=?", (decimal_text(amount), project_id))
            after = dict(conn.execute("SELECT * FROM budgets WHERE project_id=?", (project_id,)).fetchone())
            after["reason"] = reason
            self._change(conn, "budget", project_id, "adjust", before, after, actor["user_id"], at)
        return after

    def publish_document(self, session_id: str, document_id: str, effective_from: str,
                         status: str = "active") -> dict[str, Any]:
        actor = self._actor(session_id, {"admin", "finance_manager"})
        if status not in {"active", "pending", "archived"}:
            raise ValueError("制度状态仅支持 active、pending、archived")
        at = utc_now()
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM document_states WHERE document_id=?", (document_id,)).fetchone()
            if not row:
                raise KeyError("制度文档不存在，请先完成文件登记、清洗和索引")
            before = dict(row)
            if status == "active":
                conn.execute(
                    "UPDATE document_states SET status='superseded',effective_to=?,updated_at=? WHERE title=? AND document_id!=? AND status='active'",
                    (effective_from, at, row["title"], document_id),
                )
            conn.execute("UPDATE document_states SET status=?,effective_from=?,effective_to=NULL,updated_at=? WHERE document_id=?",
                         (status, effective_from, at, document_id))
            after = dict(conn.execute("SELECT * FROM document_states WHERE document_id=?", (document_id,)).fetchone())
            self._change(conn, "document", document_id, "publish", before, after, actor["user_id"], effective_from)
        return after

    def list_changes(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        actor = self._actor(session_id, {"admin", "finance_manager", "auditor"})
        with connect(self.db_path) as conn:
            return [dict(x) for x in conn.execute(
                "SELECT * FROM change_events ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 200),)
            )]
