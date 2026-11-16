"""OA 式组织权限、统一事项、流程任务和变更记录的 SQLite 底座。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ENTERPRISE_SCHEMA = """
CREATE TABLE departments (
    department_id TEXT PRIMARY KEY, name TEXT NOT NULL, cost_center TEXT NOT NULL
);
CREATE TABLE users (
    user_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, department_id TEXT NOT NULL,
    position TEXT NOT NULL, level TEXT NOT NULL, manager_id TEXT, project_id TEXT NOT NULL,
    status TEXT NOT NULL, effective_from TEXT NOT NULL, effective_to TEXT,
    FOREIGN KEY(department_id) REFERENCES departments(department_id)
);
CREATE TABLE roles (
    role_code TEXT PRIMARY KEY, role_name TEXT NOT NULL,
    knowledge_role TEXT NOT NULL, data_scope TEXT NOT NULL
);
CREATE TABLE user_roles (
    user_id TEXT NOT NULL, role_code TEXT NOT NULL, effective_from TEXT NOT NULL,
    effective_to TEXT, is_current INTEGER NOT NULL,
    PRIMARY KEY(user_id, role_code, effective_from),
    FOREIGN KEY(user_id) REFERENCES users(user_id), FOREIGN KEY(role_code) REFERENCES roles(role_code)
);
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, active INTEGER NOT NULL,
    created_at TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES users(user_id)
);
CREATE TABLE work_items (
    item_id TEXT PRIMARY KEY, scenario TEXT NOT NULL, record_id TEXT NOT NULL,
    title TEXT NOT NULL, status TEXT NOT NULL, department_id TEXT NOT NULL,
    project_id TEXT NOT NULL, amount TEXT NOT NULL, created_by TEXT NOT NULL,
    current_assignee TEXT, current_role TEXT, return_to_status TEXT,
    requires_finance_manager INTEGER NOT NULL DEFAULT 0,
    policy_version TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(scenario, record_id),
    FOREIGN KEY(created_by) REFERENCES users(user_id)
);
CREATE TABLE workflow_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, actor_id TEXT NOT NULL,
    action TEXT NOT NULL, from_status TEXT NOT NULL, to_status TEXT NOT NULL,
    comment TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(item_id) REFERENCES work_items(item_id)
);
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY, item_id TEXT NOT NULL, assignee_id TEXT NOT NULL,
    role_code TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, completed_at TEXT,
    FOREIGN KEY(item_id) REFERENCES work_items(item_id), FOREIGN KEY(assignee_id) REFERENCES users(user_id)
);
CREATE TABLE exceptions (
    exception_id TEXT PRIMARY KEY, item_id TEXT NOT NULL, code TEXT NOT NULL,
    message TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
    resolved_by TEXT, resolved_at TEXT, resolution_note TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(item_id) REFERENCES work_items(item_id)
);
CREATE TABLE supplements (
    supplement_id TEXT PRIMARY KEY, item_id TEXT NOT NULL, material_type TEXT NOT NULL,
    reference TEXT NOT NULL, note TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(item_id) REFERENCES work_items(item_id)
);
CREATE TABLE budget_reservations (
    reservation_id TEXT PRIMARY KEY, item_id TEXT NOT NULL, project_id TEXT NOT NULL,
    amount TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(item_id), FOREIGN KEY(item_id) REFERENCES work_items(item_id),
    FOREIGN KEY(project_id) REFERENCES budgets(project_id)
);
CREATE TABLE reconciliation_runs (
    run_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, bank_record_count INTEGER NOT NULL,
    ledger_record_count INTEGER NOT NULL, matched_count INTEGER NOT NULL,
    difference_count INTEGER NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE change_events (
    change_id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    change_type TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
    effective_at TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE document_states (
    document_id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
    effective_from TEXT NOT NULL, effective_to TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE attachments (
    attachment_id TEXT PRIMARY KEY, record_id TEXT NOT NULL, type TEXT NOT NULL,
    path TEXT NOT NULL, sha256 TEXT NOT NULL
);
CREATE TABLE action_requests (
    action_key TEXT PRIMARY KEY, item_id TEXT NOT NULL, actor_id TEXT NOT NULL,
    action TEXT NOT NULL, request_hash TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE payment_records (
    payment_id TEXT PRIMARY KEY, item_id TEXT NOT NULL UNIQUE, record_id TEXT NOT NULL,
    amount TEXT NOT NULL, account_fingerprint TEXT NOT NULL, reference_no TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE accounting_vouchers (
    voucher_id TEXT PRIMARY KEY, item_id TEXT NOT NULL UNIQUE, record_id TEXT NOT NULL,
    amount TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX idx_work_item_assignee ON work_items(current_assignee, status);
CREATE INDEX idx_work_item_creator ON work_items(created_by, status);
CREATE INDEX idx_task_assignee ON tasks(assignee_id, status);
CREATE INDEX idx_exception_item ON exceptions(item_id, status);
CREATE INDEX idx_change_entity ON change_events(entity_type, entity_id);
"""

ROLE_ASSIGNEE = {
    "finance_reviewer": "U-FIN-REVIEW", "finance_bp": "U-FIN-BP",
    "finance_manager": "U-FIN-HEAD", "treasury": "U-TREASURY",
    "accountant": "U-ACCOUNTANT",
}
STATUS_ROLE = {
    "pending_finance_review": "finance_reviewer", "pending_finance_manager": "finance_manager",
    "pending_budget": "finance_bp", "pending_budget_manager": "finance_manager",
    "pending_payment": "treasury", "paid": "accountant",
    "pending_accountant": "accountant", "pending_verification": "finance_manager",
}
OPEN_STATUSES = {
    "pending_department", "pending_finance_review", "pending_finance_manager",
    "pending_budget", "pending_budget_manager", "pending_payment", "paid",
    "needs_supplement", "pending_accountant", "pending_verification",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _task_id() -> str:
    return "TASK-" + uuid.uuid4().hex[:12].upper()


def add_event(conn: sqlite3.Connection, item_id: str, actor_id: str, action: str,
              from_status: str, to_status: str, comment: str = "", at: str | None = None) -> None:
    conn.execute(
        "INSERT INTO workflow_events(item_id,actor_id,action,from_status,to_status,comment,created_at) VALUES (?,?,?,?,?,?,?)",
        (item_id, actor_id, action, from_status, to_status, comment, at or utc_now()),
    )


def add_task(conn: sqlite3.Connection, item_id: str, assignee_id: str, role_code: str,
             title: str, at: str | None = None) -> None:
    conn.execute(
        "INSERT INTO tasks(task_id,item_id,assignee_id,role_code,title,status,created_at) VALUES (?,?,?,?,?,'open',?)",
        (_task_id(), item_id, assignee_id, role_code, title, at or utc_now()),
    )


def close_tasks(conn: sqlite3.Connection, item_id: str, at: str | None = None) -> None:
    conn.execute("UPDATE tasks SET status='done',completed_at=? WHERE item_id=? AND status='open'", (at or utc_now(), item_id))


def assignment_for(conn: sqlite3.Connection, status: str, created_by: str) -> tuple[str | None, str | None]:
    if status == "needs_supplement":
        return created_by, "employee"
    if status == "pending_department":
        row = conn.execute("SELECT manager_id FROM users WHERE user_id=?", (created_by,)).fetchone()
        return (row[0] if row else None), "department_manager"
    role = STATUS_ROLE.get(status)
    return (ROLE_ASSIGNEE.get(role or "") if role else None), role


def workflow_steps(conn: sqlite3.Connection, item: dict[str, Any]) -> list[dict[str, Any]]:
    if item["status"] == "cancelled":
        return []
    if item["scenario"] == "reconciliation":
        specs = [
            ("submit", "提交差异"), ("pending_accountant", "会计处理"),
            ("pending_verification", "财务负责人复核"), ("closed", "关闭"),
        ]
    else:
        specs = [("submit", "提交申请"), ("pending_department", "部门审批"), ("pending_finance_review", "财务初审")]
        if item["requires_finance_manager"]:
            specs.append(("pending_finance_manager", "财务负责人复核"))
        specs.append(("pending_budget_manager" if item["status"] == "pending_budget_manager" else "pending_budget", "预算审批"))
        specs.extend([("pending_payment", "模拟支付"), ("pending_accountant", "模拟入账"), ("closed", "关闭")])
    current = "pending_accountant" if item["status"] == "paid" else item["status"]
    if current == "needs_supplement":
        return_status = item.get("return_to_status") or "pending_finance_review"
        current_index = next((i for i, (status, _) in enumerate(specs) if status == return_status), 1)
        specs.insert(current_index, ("needs_supplement", "补充材料"))
        current = "needs_supplement"
    current_index = next((i for i, (status, _) in enumerate(specs) if status == current), len(specs) - 1)
    user_ids = {item["created_by"]}
    for status, _ in specs:
        assignee, _ = assignment_for(conn, status, item["created_by"]) if status not in {"submit", "closed"} else (item["created_by"] if status == "submit" else None, None)
        if assignee:
            user_ids.add(assignee)
    names = {}
    if user_ids:
        placeholders = ",".join("?" for _ in user_ids)
        names = {row["user_id"]: row["display_name"] for row in conn.execute(
            f"SELECT user_id,display_name FROM users WHERE user_id IN ({placeholders})", tuple(user_ids)
        )}
    steps = []
    for index, (status, label) in enumerate(specs):
        if status == "submit":
            assignee = item["created_by"]
        elif status == "closed":
            assignee = None
        else:
            assignee, _ = assignment_for(conn, status, item["created_by"])
        state = "completed" if current == "closed" or index < current_index else "current" if index == current_index else "upcoming"
        steps.append({
            "status": status, "label": label, "state": state, "assignee_id": assignee,
            "assignee_name": names.get(assignee) if assignee else None,
        })
    return steps


def create_work_item(conn: sqlite3.Connection, *, scenario: str, record_id: str, title: str,
                     status: str, department_id: str, project_id: str, amount: str,
                     created_by: str, policy_version: str, requires_finance_manager: bool = False,
                     item_id: str | None = None, at: str | None = None) -> str:
    at = at or utc_now()
    item_id = item_id or "WI-" + uuid.uuid4().hex[:12].upper()
    assignee, role = assignment_for(conn, status, created_by)
    conn.execute(
        """INSERT INTO work_items(item_id,scenario,record_id,title,status,department_id,project_id,
           amount,created_by,current_assignee,current_role,requires_finance_manager,policy_version,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item_id, scenario, record_id, title, status, department_id, project_id, amount,
         created_by, assignee, role, int(requires_finance_manager), policy_version, at, at),
    )
    add_event(conn, item_id, created_by, "submit", "draft", status, "合成单据已提交", at)
    if assignee and role:
        add_task(conn, item_id, assignee, role, title, at)
    return item_id


def _load_organization(conn: sqlite3.Connection, raw: Path) -> None:
    rows = _json(raw / "organization" / "departments.json", [])
    conn.executemany("INSERT INTO departments VALUES (?,?,?)", [
        (x["department_id"], x["name"], x["cost_center"]) for x in rows
    ])
    roles = _json(raw / "organization" / "roles.json", [])
    conn.executemany("INSERT INTO roles VALUES (?,?,?,?)", [
        (x["role_code"], x["role_name"], x["knowledge_role"], x["data_scope"]) for x in roles
    ])
    users = _json(raw / "organization" / "users.json", [])
    conn.executemany("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (x["user_id"], x["display_name"], x["department_id"], x["position"], x["level"],
         x.get("manager_id"), x["project_id"], x["status"], x["effective_from"], x.get("effective_to"))
        for x in users
    ])
    for user in users:
        for role in user["roles"]:
            conn.execute(
                "INSERT INTO user_roles(user_id,role_code,effective_from,effective_to,is_current) VALUES (?,?,?,?,1)",
                (user["user_id"], role, user["effective_from"], user.get("effective_to")),
            )


def _load_document_states(conn: sqlite3.Connection, root: Path) -> None:
    catalog = _json(root / "data" / "catalog.json", [])
    now = utc_now()
    conn.executemany("INSERT INTO document_states VALUES (?,?,?,?,?,?)", [
        (x["document_id"], x["title"], x["status"], x["effective_date"], None, now) for x in catalog
    ])


def _load_expenses(conn: sqlite3.Connection, raw: Path) -> None:
    reports = _json(raw / "expenses" / "expense_reports.json", [])
    for x in reports:
        conn.execute(
            """INSERT INTO expense_reports(report_id,city,days,level,lodging_price,lodging_total,
               meal_total,transport_total,total_amount,overlimit_amount,approval_chain,created_by_role,created_at,system)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (x["report_id"], x["city"], x["days"], x["level"], x["lodging_price"], x["lodging_total"],
             x["meal_total"], x["transport_total"], x["total_amount"], x["overlimit_amount"],
             x["approval_chain"], "employee", x["created_at"], "local_synthetic_demo"),
        )
        item_id = create_work_item(
            conn, scenario="expense", record_id=x["report_id"],
            title=f"{x['city']}差旅报销 {x['total_amount']} 元", status=x["status"],
            department_id=x["department_id"], project_id=x["project_id"], amount=x["total_amount"],
            created_by=x["created_by"], policy_version="travel-policy:2.1",
            requires_finance_manager=bool(float(x["overlimit_amount"]) > 0),
            item_id="WI-" + x["report_id"], at=x["created_at"],
        )
        if x.get("exception"):
            conn.execute(
                "INSERT INTO exceptions(exception_id,item_id,code,message,status,created_at) VALUES (?,?,?,?,?,?)",
                ("EX-" + x["report_id"], item_id, x["exception"]["code"], x["exception"]["message"],
                 x["exception"]["status"], x["created_at"]),
            )


def _load_payment_workflows(conn: sqlite3.Connection, raw: Path) -> None:
    rows = _json(raw / "transactions" / "payment_workflows.json", [])
    for x in rows:
        item_id = create_work_item(
            conn, scenario="payment", record_id=x["request_id"], title=x["title"], status=x["status"],
            department_id=x["department_id"], project_id=x["project_id"], amount=x["amount"],
            created_by=x["created_by"], policy_version="payment-policy:1.0",
            requires_finance_manager=x.get("requires_finance_manager", False),
            item_id="WI-" + x["request_id"], at=x["created_at"],
        )
        if x.get("exception"):
            conn.execute(
                "INSERT INTO exceptions(exception_id,item_id,code,message,status,created_at) VALUES (?,?,?,?,?,?)",
                ("EX-" + x["request_id"], item_id, x["exception"]["code"], x["exception"]["message"],
                 "open", x["created_at"]),
            )


def ensure_enterprise_schema(conn: sqlite3.Connection, root: Path) -> None:
    conn.executescript(ENTERPRISE_SCHEMA)
    raw = root / "data" / "raw"
    _load_organization(conn, raw)
    _load_document_states(conn, root)
    _load_expenses(conn, raw)
    _load_payment_workflows(conn, raw)
    attachments = _json(raw / "attachments" / "attachments.json", [])
    conn.executemany("INSERT INTO attachments VALUES (?,?,?,?,?)", [
        (x["attachment_id"], x["record_id"], x["type"], x["path"], x["sha256"]) for x in attachments
    ])


class EnterpriseRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def list_users(self) -> list[dict[str, Any]]:
        from .database import connect
        with connect(self.db_path) as conn:
            rows = [dict(x) for x in conn.execute(
                """SELECT u.user_id,u.display_name,u.department_id,u.position,u.level,u.status,
                   d.name AS department_name,u.project_id FROM users u
                   JOIN departments d ON d.department_id=u.department_id ORDER BY u.user_id"""
            )]
            for row in rows:
                row["roles"] = [x["role_code"] for x in conn.execute(
                    "SELECT role_code FROM user_roles WHERE user_id=? AND is_current=1", (row["user_id"],)
                )]
                row["role_names"] = [x["role_name"] for x in conn.execute(
                    """SELECT r.role_name FROM user_roles ur JOIN roles r ON r.role_code=ur.role_code
                       WHERE ur.user_id=? AND ur.is_current=1""", (row["user_id"],)
                )]
            return rows

    def create_session(self, user_id: str, session_id: str | None = None) -> dict[str, Any]:
        from .database import connect
        session_id = session_id or "enterprise-" + uuid.uuid4().hex
        with connect(self.db_path) as conn:
            user = conn.execute("SELECT status FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not user:
                raise KeyError("模拟用户不存在")
            if user["status"] != "active":
                raise PermissionError("该模拟用户已停用，不能建立新会话")
            existing = conn.execute("SELECT user_id,active FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if existing:
                if existing["user_id"] != user_id:
                    raise PermissionError("会话编号已绑定其他用户")
                if existing["active"]:
                    return self.identity(session_id)
                conn.execute("UPDATE sessions SET active=1,created_at=? WHERE session_id=?", (utc_now(), session_id))
            else:
                conn.execute("INSERT INTO sessions VALUES (?,?,1,?)", (session_id, user_id, utc_now()))
        return self.identity(session_id)

    def identity(self, session_id: str) -> dict[str, Any]:
        from .database import connect
        with connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT s.session_id,u.*,d.name AS department_name FROM sessions s
                   JOIN users u ON u.user_id=s.user_id JOIN departments d ON d.department_id=u.department_id
                   WHERE s.session_id=? AND s.active=1""", (session_id,)
            ).fetchone()
            if not row:
                raise PermissionError("会话无效或已停用，请重新选择模拟用户")
            identity = dict(row)
            if identity["status"] != "active":
                raise PermissionError("用户已离职或停用，不能继续操作")
            roles = [dict(x) for x in conn.execute(
                """SELECT r.* FROM user_roles ur JOIN roles r ON r.role_code=ur.role_code
                   WHERE ur.user_id=? AND ur.is_current=1 ORDER BY r.role_code""", (identity["user_id"],)
            )]
            identity["roles"] = [x["role_code"] for x in roles]
            identity["role_names"] = [x["role_name"] for x in roles]
            identity["data_scopes"] = [x["data_scope"] for x in roles]
            knowledge_priority = {"admin": 3, "manager": 2, "employee": 1}
            identity["knowledge_role"] = max(
                (x["knowledge_role"] for x in roles), key=lambda x: knowledge_priority[x], default="employee"
            )
            return identity

    def can_view(self, identity: dict[str, Any], item: dict[str, Any]) -> bool:
        if item["current_assignee"] == identity["user_id"] or item["created_by"] == identity["user_id"]:
            return True
        roles = set(identity["roles"])
        if roles & {"auditor", "finance_reviewer", "finance_bp", "finance_manager", "treasury", "accountant"}:
            return True
        return "department_manager" in roles and item["department_id"] == identity["department_id"]

    def list_work_items(self, identity: dict[str, Any], view: str = "visible") -> list[dict[str, Any]]:
        from .database import connect
        with connect(self.db_path) as conn:
            rows = [dict(x) for x in conn.execute(
                """SELECT w.*,u.display_name AS creator_name,a.display_name AS assignee_name
                   FROM work_items w JOIN users u ON u.user_id=w.created_by
                   LEFT JOIN users a ON a.user_id=w.current_assignee ORDER BY w.updated_at DESC"""
            )]
        if view == "tasks":
            rows = [x for x in rows if x["current_assignee"] == identity["user_id"] and x["status"] in OPEN_STATUSES]
        elif view == "requests":
            rows = [x for x in rows if x["created_by"] == identity["user_id"]]
        else:
            rows = [x for x in rows if self.can_view(identity, x)]
        return rows

    def get_work_item(self, item_id: str, identity: dict[str, Any]) -> dict[str, Any]:
        from .database import connect
        with connect(self.db_path) as conn:
            item = conn.execute(
                """SELECT w.*,u.display_name AS creator_name,a.display_name AS assignee_name
                   FROM work_items w JOIN users u ON u.user_id=w.created_by
                   LEFT JOIN users a ON a.user_id=w.current_assignee WHERE w.item_id=?""", (item_id,)
            ).fetchone()
            if not item:
                raise KeyError("事项不存在")
            result = dict(item)
            if not self.can_view(identity, result):
                raise PermissionError("当前用户无权查看该事项")
            result["events"] = [dict(x) for x in conn.execute(
                """SELECT e.*,u.display_name AS actor_name FROM workflow_events e
                   JOIN users u ON u.user_id=e.actor_id WHERE e.item_id=? ORDER BY e.event_id""", (item_id,)
            )]
            result["exceptions"] = [dict(x) for x in conn.execute(
                "SELECT * FROM exceptions WHERE item_id=? ORDER BY created_at", (item_id,)
            )]
            result["supplements"] = [dict(x) for x in conn.execute(
                "SELECT * FROM supplements WHERE item_id=? ORDER BY created_at", (item_id,)
            )]
            reservation = conn.execute("SELECT * FROM budget_reservations WHERE item_id=?", (item_id,)).fetchone()
            result["budget_reservation"] = dict(reservation) if reservation else None
            budget = conn.execute("SELECT * FROM budgets WHERE project_id=?", (result["project_id"],)).fetchone()
            if budget:
                available = Decimal(budget["annual_budget"]) - Decimal(budget["committed"]) - Decimal(budget["actual"]) - Decimal(budget["frozen"])
                result["budget_summary"] = {key: budget[key] for key in ("project_id", "annual_budget", "committed", "actual", "frozen")}
                result["budget_summary"]["available"] = format(available, ".2f")
            result["tasks"] = [dict(x) for x in conn.execute(
                """SELECT t.*,u.display_name AS assignee_name FROM tasks t
                   JOIN users u ON u.user_id=t.assignee_id WHERE t.item_id=? ORDER BY t.created_at""", (item_id,)
            )]
            result["workflow_steps"] = workflow_steps(conn, result)
            result["attachments"] = [dict(x) for x in conn.execute(
                "SELECT * FROM attachments WHERE record_id IN (?,?) ORDER BY attachment_id",
                (result["record_id"], result.get("contract_id") or ""),
            )]
            result["payments"] = [dict(x) for x in conn.execute(
                "SELECT * FROM payment_records WHERE item_id=?", (item_id,)
            )]
            result["vouchers"] = [dict(x) for x in conn.execute(
                "SELECT * FROM accounting_vouchers WHERE item_id=?", (item_id,)
            )]
            if result["scenario"] == "expense":
                row = conn.execute("SELECT * FROM expense_reports WHERE report_id=?", (result["record_id"],)).fetchone()
                result["business"] = dict(row) if row else {}
            elif result["scenario"] == "payment":
                row = conn.execute(
                    """SELECT p.*,c.title AS contract_title,c.vendor_id,c.total_amount AS contract_amount,
                       v.vendor_name,v.status AS vendor_status,v.risk_level FROM payment_requests p
                       JOIN contracts c ON c.contract_id=p.contract_id JOIN vendors v ON v.vendor_id=c.vendor_id
                       WHERE p.request_id=?""", (result["record_id"],)
                ).fetchone()
                result["business"] = dict(row) if row else {}
                result["attachments"] = [dict(x) for x in conn.execute(
                    "SELECT * FROM attachments WHERE record_id IN (?,?) ORDER BY attachment_id",
                    (result["record_id"], result["business"].get("contract_id", "")),
                )]
            else:
                result["business"] = {}
            return result
