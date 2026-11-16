"""由 SQLite 明确关系生成业务解释图谱；不负责交易计算或替代业务真源。"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

from .database import build_database, connect, database_path
from .enterprise_database import EnterpriseRepository
from .ingestion import ROOT


class FinanceGraphService:
    def __init__(self, root: Path = ROOT, db_path: Path | None = None):
        self.root = root
        self.db_path = db_path or database_path(root)
        build_database(root, self.db_path)
        self.repository = EnterpriseRepository(self.db_path)

    @staticmethod
    def _node(nodes: dict[str, dict], node_id: str, kind: str, label: str, **properties) -> None:
        nodes[node_id] = {"id": node_id, "type": kind, "label": label, "properties": properties}

    @staticmethod
    def _edge(edges: list[dict], source: str, target: str, kind: str) -> None:
        edges.append({"source": source, "target": target, "type": kind})

    def build(self) -> dict[str, Any]:
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        with connect(self.db_path) as conn:
            for x in conn.execute("SELECT * FROM vendors"):
                self._node(nodes, x["vendor_id"], "vendor", x["vendor_name"], status=x["status"], risk_level=x["risk_level"])
            for x in conn.execute("SELECT * FROM contracts"):
                self._node(nodes, x["contract_id"], "contract", x["title"], amount=x["total_amount"], status=x["status"])
                self._edge(edges, x["vendor_id"], x["contract_id"], "签订")
                self._node(nodes, x["project_id"], "budget", x["project_id"])
                self._edge(edges, x["contract_id"], x["project_id"], "占用预算")
            for x in conn.execute("SELECT * FROM contract_milestones"):
                node_id = f"{x['contract_id']}:{x['code']}"
                self._node(nodes, node_id, "milestone", x["name"], ratio=x["ratio"])
                self._edge(edges, x["contract_id"], node_id, "包含里程碑")
            for x in conn.execute("SELECT * FROM payment_requests"):
                self._node(nodes, x["request_id"], "payment_request", x["request_id"], amount=x["amount"])
                self._edge(edges, x["contract_id"], x["request_id"], "产生申请")
                self._edge(edges, x["request_id"], f"{x['contract_id']}:{x['milestone_code']}", "对应里程碑")
                if x["invoice_id"]:
                    self._edge(edges, x["request_id"], x["invoice_id"], "关联发票")
                if x["acceptance_id"]:
                    self._edge(edges, x["request_id"], x["acceptance_id"], "关联验收")
            for x in conn.execute("SELECT * FROM invoices"):
                self._node(nodes, x["invoice_id"], "invoice", x["invoice_number"], amount=x["amount"], status=x["status"])
                self._edge(edges, x["request_id"], x["invoice_id"], "发票")
            for x in conn.execute("SELECT * FROM acceptances"):
                self._node(nodes, x["acceptance_id"], "acceptance", x["acceptance_id"], status=x["status"])
                self._edge(edges, x["acceptance_id"], x["contract_id"], "验收合同")
            for x in conn.execute("SELECT * FROM expense_reports"):
                self._node(nodes, x["report_id"], "expense", x["report_id"], amount=x["total_amount"], city=x["city"])
            for x in conn.execute("SELECT * FROM work_items"):
                self._node(nodes, x["item_id"], "work_item", x["title"], status=x["status"], version=x["version"])
                self._edge(edges, x["record_id"] if x["scenario"] != "reconciliation" else x["item_id"], x["item_id"], "流程事项") if x["scenario"] != "reconciliation" else None
                self._node(nodes, x["created_by"], "user", x["created_by"])
                self._edge(edges, x["created_by"], x["item_id"], "提交")
                self._node(nodes, x["project_id"], "budget", x["project_id"])
                self._edge(edges, x["item_id"], x["project_id"], "预算项目")
                if x["current_assignee"]:
                    self._node(nodes, x["current_assignee"], "user", x["current_assignee"])
                    self._edge(edges, x["item_id"], x["current_assignee"], "当前处理人")
            for x in conn.execute("SELECT * FROM payment_records"):
                self._node(nodes, x["payment_id"], "payment", x["reference_no"], amount=x["amount"], status=x["status"])
                self._edge(edges, x["item_id"], x["payment_id"], "模拟支付")
            for x in conn.execute("SELECT * FROM accounting_vouchers"):
                self._node(nodes, x["voucher_id"], "voucher", x["voucher_id"], amount=x["amount"], status=x["status"])
                self._edge(edges, x["item_id"], x["voucher_id"], "模拟入账")
            for x in conn.execute("SELECT * FROM bank_transactions"):
                self._node(nodes, x["transaction_id"], "bank", x["transaction_id"], amount=x["amount"], account=x["account_id"])
            for x in conn.execute("SELECT * FROM ledger_entries"):
                self._node(nodes, x["voucher_id"], "ledger", x["voucher_id"], amount=x["amount"], account=x["account_id"])
                self._edge(edges, x["external_ref"], x["voucher_id"], "对账匹配")
        return {"nodes": list(nodes.values()), "edges": edges, "source": "sqlite_deterministic_relations"}

    def query(self, session_id: str, record_id: str | None = None, depth: int = 4) -> dict[str, Any]:
        identity = self.repository.identity(session_id)
        if record_id:
            with connect(self.db_path) as conn:
                item = conn.execute(
                    "SELECT * FROM work_items WHERE item_id=? OR record_id=? OR record_id LIKE ? LIMIT 1",
                    (record_id, record_id, "%:" + record_id + ":%"),
                ).fetchone()
                request = conn.execute("SELECT requester FROM payment_requests WHERE request_id=?", (record_id,)).fetchone()
            if item and not self.repository.can_view(identity, dict(item)):
                raise PermissionError("无权查看该事项关系图谱")
            if not item and (not request or request["requester"] != identity["user_id"]) and not set(identity["roles"]) & {
                "auditor", "finance_reviewer", "finance_bp", "finance_manager", "treasury", "accountant"
            }:
                raise PermissionError("无权查看该业务关系图谱")
        elif not set(identity["roles"]) & {"auditor", "finance_reviewer", "finance_bp", "finance_manager", "treasury", "accountant"}:
            raise PermissionError("普通员工请按本人单据查询关系图谱")
        graph = self.build()
        if not record_id:
            return graph
        if record_id not in {x["id"] for x in graph["nodes"]}:
            matches = [x["id"] for x in graph["nodes"] if record_id in x["id"]]
            if not matches:
                raise KeyError("图谱中未找到该记录")
            record_id = matches[0]
        adjacency: dict[str, set[str]] = {}
        for edge in graph["edges"]:
            adjacency.setdefault(edge["source"], set()).add(edge["target"])
            adjacency.setdefault(edge["target"], set()).add(edge["source"])
        seen = {record_id}
        queue = deque([(record_id, 0)])
        while queue:
            current, level = queue.popleft()
            if level >= min(max(depth, 1), 6):
                continue
            for neighbor in adjacency.get(current, set()):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, level + 1))
        return {"nodes": [x for x in graph["nodes"] if x["id"] in seen],
                "edges": [x for x in graph["edges"] if x["source"] in seen and x["target"] in seen],
                "center_id": record_id, "source": graph["source"]}
