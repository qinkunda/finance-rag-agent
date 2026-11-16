from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_audit_events (
    audit_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    question TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    result_summary TEXT NOT NULL,
    status TEXT NOT NULL,
    model_name TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_audit_created ON agent_audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_agent_audit_request ON agent_audit_events(request_id);
CREATE INDEX IF NOT EXISTS idx_agent_audit_session ON agent_audit_events(session_id);
"""

_SENSITIVE_KEYS = {"api_key", "apikey", "token", "secret", "password", "authorization"}


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else _safe_value(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _json(value: Any) -> str:
    return json.dumps(_safe_value(value), ensure_ascii=False, default=str)


class AgentAuditTrail:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)

    def record(self, *, request_id: str, session_id: str, identity: dict[str, Any], question: str,
               tool_name: str, arguments: Any, result: Any, status: str, model_name: str = "",
               execution_mode: str = "rules", started_at: float | None = None) -> str:
        audit_id = "AUD-" + uuid.uuid4().hex[:16].upper()
        latency_ms = round((time.monotonic() - started_at) * 1000) if started_at is not None else 0
        result_text = result if isinstance(result, str) else _json(result)
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO agent_audit_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (audit_id, request_id, session_id, identity.get("user_id", ""),
                 identity.get("display_name", identity.get("user_id", "")), question[:1000], tool_name,
                 _json(arguments), result_text[:4000], status, model_name, execution_mode, latency_ms,
                 datetime.now(timezone.utc).isoformat()),
            )
        return audit_id

    def list_events(self, *, limit: int = 50, session_id: str | None = None,
                    tool_name: str | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if session_id:
            clauses.append("session_id=?")
            params.append(session_id)
        if tool_name:
            clauses.append("tool_name=?")
            params.append(tool_name)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(min(max(int(limit), 1), 200))
        with connect(self.db_path) as conn:
            return [dict(row) for row in conn.execute(
                f"SELECT * FROM agent_audit_events{where} ORDER BY created_at DESC,audit_id DESC LIMIT ?", params
            )]

    def count(self) -> int:
        with connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM agent_audit_events").fetchone()[0]
