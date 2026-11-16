"""统一合成业务库：原始 JSON/CSV 入 SQLite，平台与 MCP 共用同一数据源。"""
from __future__ import annotations

import csv
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ingestion import ROOT

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE vendors (
    vendor_id TEXT PRIMARY KEY, vendor_name TEXT NOT NULL,
    account_fingerprint TEXT NOT NULL, status TEXT NOT NULL, risk_level TEXT NOT NULL
);
CREATE TABLE budgets (
    project_id TEXT PRIMARY KEY, department TEXT NOT NULL, cost_center TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL, annual_budget TEXT NOT NULL, committed TEXT NOT NULL,
    actual TEXT NOT NULL, frozen TEXT NOT NULL
);
CREATE TABLE contracts (
    contract_id TEXT PRIMARY KEY, vendor_id TEXT NOT NULL, project_id TEXT NOT NULL,
    title TEXT NOT NULL, total_amount TEXT NOT NULL, currency TEXT NOT NULL,
    approved INTEGER NOT NULL, status TEXT NOT NULL, signed_at TEXT NOT NULL,
    FOREIGN KEY(vendor_id) REFERENCES vendors(vendor_id),
    FOREIGN KEY(project_id) REFERENCES budgets(project_id)
);
CREATE TABLE contract_milestones (
    contract_id TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
    ratio TEXT NOT NULL, requires_json TEXT NOT NULL,
    PRIMARY KEY(contract_id, code), FOREIGN KEY(contract_id) REFERENCES contracts(contract_id)
);
CREATE TABLE payment_requests (
    request_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, milestone_code TEXT NOT NULL,
    amount TEXT NOT NULL, invoice_id TEXT, acceptance_id TEXT,
    warranty_expired INTEGER NOT NULL, account_fingerprint TEXT NOT NULL,
    requester TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(contract_id) REFERENCES contracts(contract_id)
);
CREATE TABLE invoices (
    invoice_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, invoice_number TEXT NOT NULL,
    status TEXT NOT NULL, amount TEXT NOT NULL, tax_rate TEXT NOT NULL, issue_date TEXT NOT NULL,
    FOREIGN KEY(request_id) REFERENCES payment_requests(request_id)
);
CREATE TABLE acceptances (
    acceptance_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, milestone_code TEXT NOT NULL,
    status TEXT NOT NULL, accepted_at TEXT,
    FOREIGN KEY(contract_id) REFERENCES contracts(contract_id)
);
CREATE TABLE budget_requests (
    request_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, amount TEXT NOT NULL,
    reason TEXT NOT NULL, cost_type TEXT NOT NULL, requester TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES budgets(project_id)
);
CREATE TABLE bank_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id TEXT NOT NULL, account_id TEXT NOT NULL,
    amount TEXT NOT NULL, currency TEXT NOT NULL, transaction_date TEXT NOT NULL, description TEXT NOT NULL
);
CREATE TABLE ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, voucher_id TEXT NOT NULL, external_ref TEXT NOT NULL,
    account_id TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL, posting_date TEXT NOT NULL
);
CREATE TABLE dim_city_tier (city TEXT PRIMARY KEY, tier TEXT NOT NULL);
CREATE TABLE dim_expense_standard (
    expense_type TEXT NOT NULL, level TEXT NOT NULL, tier TEXT NOT NULL, limit_amount TEXT NOT NULL,
    PRIMARY KEY(expense_type, level, tier)
);
CREATE TABLE dim_approval_chain (
    role TEXT NOT NULL, node_order INTEGER NOT NULL, node_name TEXT NOT NULL,
    PRIMARY KEY(role, node_order)
);
CREATE TABLE tickets (
    ticket_id TEXT PRIMARY KEY, scenario TEXT NOT NULL, record_id TEXT NOT NULL,
    summary TEXT NOT NULL, status TEXT NOT NULL, created_by_role TEXT NOT NULL,
    payload_json TEXT NOT NULL, created_at TEXT NOT NULL, system TEXT NOT NULL
);
CREATE TABLE expense_reports (
    report_id TEXT PRIMARY KEY, city TEXT NOT NULL, days INTEGER NOT NULL, level TEXT NOT NULL,
    lodging_price TEXT NOT NULL, lodging_total TEXT NOT NULL, meal_total TEXT NOT NULL,
    transport_total TEXT NOT NULL, total_amount TEXT NOT NULL, overlimit_amount TEXT NOT NULL,
    approval_chain TEXT NOT NULL, created_by_role TEXT NOT NULL, created_at TEXT NOT NULL,
    system TEXT NOT NULL
);
CREATE TABLE audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, channel TEXT NOT NULL,
    tool TEXT NOT NULL, user_role TEXT NOT NULL, status TEXT NOT NULL, record_id TEXT NOT NULL,
    payload_stored INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_bank_transaction_id ON bank_transactions(transaction_id);
CREATE INDEX idx_ledger_external_ref ON ledger_entries(external_ref);
CREATE INDEX idx_payment_contract ON payment_requests(contract_id);
CREATE INDEX idx_audit_record ON audit_events(record_id);
"""

TRAVEL_CITIES = [
    ("北京", "一类"), ("上海", "一类"), ("广州", "一类"), ("深圳", "一类"),
    ("成都", "新一线"), ("武汉", "新一线"), ("杭州", "新一线"),
    ("南京", "新一线"), ("西安", "二类"), ("长沙", "二类"),
]
TRAVEL_STANDARDS = [
    ("住宿", "普通员工", "一类", "500"), ("住宿", "普通员工", "新一线", "400"),
    ("住宿", "普通员工", "二类", "350"), ("住宿", "普通员工", "三类", "300"),
    ("住宿", "经理级", "一类", "700"), ("住宿", "经理级", "新一线", "600"),
    ("住宿", "经理级", "二类", "500"), ("住宿", "经理级", "三类", "450"),
    ("住宿", "总监级", "一类", "900"), ("住宿", "总监级", "新一线", "800"),
    ("住宿", "总监级", "二类", "700"), ("住宿", "总监级", "三类", "600"),
    ("伙食", "通用", "一类", "150"), ("伙食", "通用", "新一线", "120"),
    ("伙食", "通用", "二类", "100"), ("伙食", "通用", "三类", "80"),
    ("市内交通", "通用", "通用", "80"),
]
TRAVEL_APPROVALS = [
    ("普通员工", 1, "直属上级"), ("普通员工", 2, "部门负责人"),
    ("经理级", 1, "部门负责人"), ("经理级", 2, "中心总监"),
    ("总监级", 1, "中心总监"), ("总监级", 2, "财务负责人"),
]


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def database_path(root: Path = ROOT) -> Path:
    return root / "data" / "finance_demo.db"


def build_database(root: Path = ROOT, db_path: Path | None = None, rebuild: bool = False) -> dict[str, int]:
    """从显式登记的合成原始文件重建业务库；不读取项目外文件。"""
    root = root.resolve()
    db_path = db_path or database_path(root)
    if db_path.exists() and not rebuild:
        return database_stats(db_path)
    if rebuild and db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw = root / "data" / "raw"
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        vendors = _json(raw / "vendors" / "vendors.json")
        budgets = _csv(raw / "budget" / "budgets.csv")
        contracts = _json(raw / "contracts" / "contracts.json")
        payments = _json(raw / "transactions" / "payment_requests.json")
        invoices = _json(raw / "transactions" / "invoices.json")
        acceptances = _json(raw / "transactions" / "acceptances.json")
        budget_requests = _json(raw / "budget" / "budget_requests.json")
        bank = _csv(raw / "reconciliation" / "bank_statement.csv")
        ledger = _csv(raw / "reconciliation" / "general_ledger.csv")

        conn.executemany("INSERT INTO vendors VALUES (?,?,?,?,?)", [
            (x["vendor_id"], x["vendor_name"], x["account_fingerprint"], x["status"], x["risk_level"])
            for x in vendors
        ])
        conn.executemany("INSERT INTO budgets VALUES (?,?,?,?,?,?,?,?)", [
            (x["project_id"], x["department"], x["cost_center"], int(x["fiscal_year"]),
             x["annual_budget"], x["committed"], x["actual"], x["frozen"])
            for x in budgets
        ])
        conn.executemany("INSERT INTO contracts VALUES (?,?,?,?,?,?,?,?,?)", [
            (x["contract_id"], x["vendor_id"], x["project_id"], x["title"], x["total_amount"],
             x["currency"], int(x["approved"]), x["status"], x["signed_at"])
            for x in contracts
        ])
        conn.executemany("INSERT INTO contract_milestones VALUES (?,?,?,?,?)", [
            (contract["contract_id"], m["code"], m["name"], m["ratio"], json.dumps(m["requires"], ensure_ascii=False))
            for contract in contracts for m in contract["milestones"]
        ])
        conn.executemany("INSERT INTO payment_requests VALUES (?,?,?,?,?,?,?,?,?,?)", [
            (x["request_id"], x["contract_id"], x["milestone"], x["amount"], x.get("invoice_id"),
             x.get("acceptance_id"), int(x.get("warranty_expired", False)), x["account_fingerprint"],
             x["requester"], x["created_at"])
            for x in payments
        ])
        conn.executemany("INSERT INTO invoices VALUES (?,?,?,?,?,?,?)", [
            (x["invoice_id"], x["request_id"], x["invoice_number"], x["status"], x["amount"],
             x["tax_rate"], x["issue_date"])
            for x in invoices
        ])
        conn.executemany("INSERT INTO acceptances VALUES (?,?,?,?,?)", [
            (x["acceptance_id"], x["contract_id"], x["milestone"], x["status"], x.get("accepted_at"))
            for x in acceptances
        ])
        conn.executemany("INSERT INTO budget_requests VALUES (?,?,?,?,?,?,?)", [
            (x["request_id"], x["project_id"], x["amount"], x["reason"], x["cost_type"],
             x["requester"], x["created_at"])
            for x in budget_requests
        ])
        conn.executemany(
            "INSERT INTO bank_transactions(transaction_id,account_id,amount,currency,transaction_date,description) VALUES (?,?,?,?,?,?)",
            [(x["transaction_id"], x["account_id"], x["amount"], x["currency"], x["date"], x["description"]) for x in bank],
        )
        conn.executemany(
            "INSERT INTO ledger_entries(voucher_id,external_ref,account_id,amount,currency,posting_date) VALUES (?,?,?,?,?,?)",
            [(x["voucher_id"], x["external_ref"], x["account_id"], x["amount"], x["currency"], x["date"]) for x in ledger],
        )
        conn.executemany("INSERT INTO dim_city_tier VALUES (?,?)", TRAVEL_CITIES)
        conn.executemany("INSERT INTO dim_expense_standard VALUES (?,?,?,?)", TRAVEL_STANDARDS)
        conn.executemany("INSERT INTO dim_approval_chain VALUES (?,?,?)", TRAVEL_APPROVALS)
        from .enterprise_database import ensure_enterprise_schema
        ensure_enterprise_schema(conn, root)
        conn.commit()
    except Exception:
        conn.close()
        db_path.unlink(missing_ok=True)
        raise
    finally:
        conn.close()
    return database_stats(db_path)


def database_stats(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        return {}
    conn = connect(db_path)
    try:
        tables = (
            "vendors", "contracts", "contract_milestones", "payment_requests", "invoices",
            "acceptances", "budgets", "budget_requests", "bank_transactions", "ledger_entries",
            "tickets", "expense_reports",
        )
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
    finally:
        conn.close()


class FinanceRepository:
    def __init__(self, root: Path = ROOT, db_path: Path | None = None):
        self.root = root
        self.db_path = db_path or database_path(root)
        build_database(root, self.db_path)

    def stats(self) -> dict[str, int]:
        return database_stats(self.db_path)

    def audit(self, channel: str, tool: str, role: str, status: str, record_id: str = "") -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO audit_events(timestamp,channel,tool,user_role,status,record_id,payload_stored) VALUES (?,?,?,?,?,?,0)",
                (datetime.now(timezone.utc).isoformat(), channel, tool, role, status, record_id),
            )

    def create_ticket(
        self, scenario: str, summary: str, role: str, record_id: str = "",
        status: str = "pending", payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ticket_id = "TK-" + uuid.uuid4().hex[:12].upper()
        created_at = datetime.now(timezone.utc).isoformat()
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?)",
                (ticket_id, scenario, record_id, summary, status, role,
                 json.dumps(payload or {}, ensure_ascii=False), created_at, "local_synthetic_demo"),
            )
            conn.execute(
                "INSERT INTO audit_events(timestamp,channel,tool,user_role,status,record_id,payload_stored) VALUES (?,?,?,?,?,?,0)",
                (created_at, "platform", "create_finance_ticket", role, "created", ticket_id),
            )
        return {
            "status": "created", "created": True, "ticket_id": ticket_id,
            "record_id": record_id, "system": "local_synthetic_demo", "created_at": created_at,
        }

    def create_expense_report(self, precheck: dict[str, Any], role: str) -> dict[str, Any]:
        report_id = "BX-" + uuid.uuid4().hex[:12].upper()
        created_at = datetime.now(timezone.utc).isoformat()
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO expense_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (report_id, precheck["city"], precheck["days"], precheck["level"],
                 precheck["lodging_price"], precheck["lodging_total"], precheck["meal_total"],
                 precheck["transport_total"], precheck["total_amount"], precheck["overlimit_amount"],
                 "|".join(precheck["approvers"]), role, created_at, "local_synthetic_demo"),
            )
            conn.execute(
                "INSERT INTO audit_events(timestamp,channel,tool,user_role,status,record_id,payload_stored) VALUES (?,?,?,?,?,?,0)",
                (created_at, "platform", "create_expense_report", role, "created", report_id),
            )
        return {
            "status": "created", "created": True, "report_id": report_id,
            "system": "local_synthetic_demo", "created_at": created_at,
        }
