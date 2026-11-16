from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

os.environ["FINANCE_OFFLINE_DEMO"] = "true"
os.environ["FINANCE_RETRIEVAL_MODE"] = "lexical"
os.environ["FINANCE_LLM_API_KEY"] = ""  # 测试隔离：强制走离线规则引擎，不触发真实 LLM 网关

from api import app
from finance_platform.changes import ChangeService
from finance_platform.database import build_database, connect
from finance_platform.enterprise_database import EnterpriseRepository
from finance_platform.graph import FinanceGraphService
from finance_platform.ingestion import ROOT
from finance_platform.orchestrator import UnifiedFinancePlatform
from finance_platform.policy_search import PolicySearch
from finance_platform.scenarios import FinanceData, PaymentService, money
from finance_platform.workflow import WorkflowConflictError, WorkflowService


class EnterprisePlatformTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "enterprise.db"
        build_database(ROOT, self.db, rebuild=True)
        self.workflow = WorkflowService(ROOT, self.db)
        self.repo = EnterpriseRepository(self.db)
        self.sessions = {}

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, user_id: str) -> str:
        if user_id not in self.sessions:
            self.sessions[user_id] = self.repo.create_session(user_id)["session_id"]
        return self.sessions[user_id]

    def act(self, user_id: str, item_id: str, action: str, version: int, comment: str = "", payload=None):
        return self.workflow.act(
            self.session(user_id), item_id, action, expected_version=version,
            idempotency_key="TEST-" + uuid.uuid4().hex, comment=comment, payload=payload or {},
        )

    def test_01_identity_scope_and_admin_separation(self):
        employee = self.repo.identity(self.session("U-EMP-001"))
        own = self.repo.list_work_items(employee)
        self.assertTrue(own)
        self.assertTrue(all(x["created_by"] == "U-EMP-001" for x in own))
        manager = self.repo.identity(self.session("U-MGR-SCM"))
        department = self.repo.list_work_items(manager)
        self.assertTrue(all(x["department_id"] == "D-SCM" for x in department))
        admin = self.repo.identity(self.session("U-ADMIN"))
        self.assertEqual(self.repo.list_work_items(admin), [])
        with self.assertRaises(PermissionError):
            self.repo.create_session("U-LEAVER")

    def test_02_payment_full_flow_budget_idempotency_and_stale_version(self):
        with connect(self.db) as conn:
            before = dict(conn.execute("SELECT * FROM budgets WHERE project_id='LOG-2026'").fetchone())
        self.act("U-FIN-REVIEW", "WI-PR-001", "approve", 1)
        with self.assertRaises(WorkflowConflictError):
            self.act("U-FIN-BP", "WI-PR-001", "approve", 1)
        self.act("U-FIN-BP", "WI-PR-001", "approve", 2)
        key = "PAY-" + uuid.uuid4().hex
        paid = self.workflow.act(self.session("U-TREASURY"), "WI-PR-001", "pay",
                                 expected_version=3, idempotency_key=key)
        replay = self.workflow.act(self.session("U-TREASURY"), "WI-PR-001", "pay",
                                   expected_version=3, idempotency_key=key)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["status"], paid["status"])
        self.act("U-ACCOUNTANT", "WI-PR-001", "post", 4)
        item = self.repo.get_work_item("WI-PR-001", self.repo.identity(self.session("U-ACCOUNTANT")))
        self.assertEqual(item["status"], "closed")
        self.assertEqual(len(item["payments"]), 1)
        self.assertEqual(len(item["vouchers"]), 1)
        with connect(self.db) as conn:
            after = dict(conn.execute("SELECT * FROM budgets WHERE project_id='LOG-2026'").fetchone())
        self.assertEqual(money(after["actual"]) - money(before["actual"]), money("30000"))
        self.assertEqual(after["frozen"], before["frozen"])

    def test_03_return_supplement_releases_and_reactivates_budget(self):
        self.act("U-FIN-REVIEW", "WI-PR-001", "approve", 1)
        with connect(self.db) as conn:
            reserved = conn.execute("SELECT frozen FROM budgets WHERE project_id='LOG-2026'").fetchone()[0]
            self.assertEqual(reserved, "30000.00")
        self.act("U-FIN-BP", "WI-PR-001", "return", 2, "请补充合同付款说明")
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT frozen FROM budgets WHERE project_id='LOG-2026'").fetchone()[0], "0.00")
            self.assertEqual(conn.execute("SELECT status FROM budget_reservations WHERE item_id='WI-PR-001'").fetchone()[0], "released")
        self.act("U-EMP-003", "WI-PR-001", "supplement", 3, "已补充", {"reference": "SUP-PDF-001"})
        item = self.repo.get_work_item("WI-PR-001", self.repo.identity(self.session("U-EMP-003")))
        self.assertEqual(item["status"], "pending_budget")
        self.assertEqual(item["budget_reservation"]["status"], "reserved")
        self.assertTrue(any(x["status"] == "resolved" for x in item["exceptions"]))

    def test_04_expense_uses_identity_level_and_closes(self):
        payload = {"city": "北京", "days": 3, "lodging_price": "450"}
        with connect(self.db) as conn:
            before_count = conn.execute("SELECT COUNT(*) FROM expense_reports").fetchone()[0]
        preview = self.workflow.precheck_expense(self.session("U-EMP-001"), payload)
        self.assertEqual(preview["total_amount"], "1590.00")
        self.assertEqual(preview["next_step"]["assignee_name"], "王衡")
        self.assertEqual(preview["workflow_steps"][1]["assignee_name"], "王衡")
        self.assertFalse(any(x["assignee_name"] == "何谨" for x in preview["workflow_steps"]))
        high_preview = self.workflow.precheck_expense(self.session("U-EMP-001"), {**payload, "lodging_price": "550"})
        self.assertTrue(any(x["assignee_name"] == "何谨" for x in high_preview["workflow_steps"]))
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM expense_reports").fetchone()[0], before_count)
        created = self.workflow.create_expense(self.session("U-EMP-001"), payload)
        item_id = created["item_id"]
        self.assertEqual(created["business"]["level"], "普通员工")
        self.assertEqual(created["amount"], "1590.00")
        self.assertEqual(created["creator_name"], "林晓")
        self.assertEqual(created["assignee_name"], "王衡")
        self.assertIn("available", created["budget_summary"])
        approved = self.act("U-MGR-SCM", item_id, "approve", 1)
        self.assertEqual(approved["assignee_name"], "孙岚")
        self.act("U-FIN-REVIEW", item_id, "approve", 2)
        self.act("U-FIN-BP", item_id, "approve", 3)
        self.act("U-TREASURY", item_id, "pay", 4)
        self.act("U-ACCOUNTANT", item_id, "post", 5)
        self.assertEqual(self.repo.get_work_item(item_id, self.repo.identity(self.session("U-EMP-001")))["status"], "closed")

    def test_05_reconciliation_difference_resolution(self):
        result = self.workflow.run_reconciliation(self.session("U-ACCOUNTANT"), "CNY-OPER-002")
        self.assertEqual(result["analysis"]["matched_count"], 56)
        self.assertEqual(len(result["item_ids"]), 2)
        self.assertEqual({x["record_id"] for x in result["items"]}, {"BK-004", "BK-007"})
        runs = self.workflow.list_reconciliation_runs(self.session("U-ACCOUNTANT"))
        self.assertEqual(runs[0]["run_id"], result["run_id"])
        self.assertEqual((runs[0]["item_count"], runs[0]["closed_count"]), (2, 0))
        for index, item_id in enumerate(result["item_ids"]):
            self.act("U-ACCOUNTANT", item_id, "resolve", 1, "已核对合成原始记录并完成调整")
            self.act("U-FIN-HEAD", item_id, "approve", 2, "复核通过")
            item = self.repo.get_work_item(item_id, self.repo.identity(self.session("U-AUDITOR")))
            self.assertEqual(item["status"], "closed")
            self.assertTrue(all(x["status"] == "resolved" for x in item["exceptions"]))
        self.assertEqual(self.workflow.list_reconciliation_runs(self.session("U-ACCOUNTANT"))[0]["closed_count"], 2)

    def test_06_deactivation_reassigns_tasks_and_disables_session(self):
        old_session = self.session("U-FIN-REVIEW")
        changes = ChangeService(ROOT, self.db)
        with self.assertRaisesRegex(ValueError, "接管人不能与停用用户相同"):
            changes.deactivate_user(self.session("U-ADMIN"), "U-FIN-REVIEW", "U-FIN-REVIEW")
        result = changes.deactivate_user(self.session("U-ADMIN"), "U-FIN-REVIEW", "U-FIN-REVIEW-BACK")
        self.assertGreater(result["reassigned_count"], 0)
        with self.assertRaises(PermissionError):
            self.repo.identity(old_session)
        item = self.repo.get_work_item("WI-PR-001", self.repo.identity(self.session("U-FIN-REVIEW-BACK")))
        self.assertEqual(item["current_assignee"], "U-FIN-REVIEW-BACK")
        self.assertEqual(item["version"], 2)
        self.assertTrue(any(x["action"] == "submit" for x in item["events"]))
        self.act("U-FIN-REVIEW-BACK", "WI-PR-001", "approve", 2)

    def test_07_role_and_vendor_changes_take_effect(self):
        changes = ChangeService(ROOT, self.db)
        changed = changes.change_user(self.session("U-ADMIN"), "U-EMP-002",
                                      {"roles": ["employee", "department_manager"]})
        self.assertIn("department_manager", changed["roles"])
        identity = self.repo.identity(self.session("U-EMP-002"))
        self.assertEqual(identity["knowledge_role"], "manager")
        data = FinanceData(ROOT, self.db)
        request = data.payment_requests["PR-001"]
        vendor_id = data.contracts[request["contract_id"]]["vendor_id"]
        changes.change_vendor(self.session("U-FIN-HEAD"), vendor_id, {"status": "frozen"})
        data.reload()
        self.assertIn("vendor_inactive", [x["code"] for x in PaymentService(data).check("PR-001")["findings"]])
        history = changes.list_changes(self.session("U-AUDITOR"))
        self.assertGreaterEqual(len(history), 2)

    def test_08_policy_version_effective_date_and_history(self):
        changes = ChangeService(ROOT, self.db)
        search = PolicySearch(ROOT, self.db)
        before = search.search("一类城市普通员工住宿标准", "employee", top_k=10)
        self.assertNotIn("POL-TRAVEL-2027-V3-0", [x["document_id"] for x in before])
        changes.publish_document(self.session("U-FIN-HEAD"), "POL-TRAVEL-2027-V3-0", "2027-01-01")
        current = search.search("一类城市普通员工住宿标准", "employee", top_k=10)
        future = search.search("一类城市普通员工住宿标准", "employee", top_k=10, as_of="2027-01-02")
        self.assertNotIn("POL-TRAVEL-2027-V3-0", [x["document_id"] for x in current])
        self.assertIn("POL-TRAVEL-2026", [x["document_id"] for x in current])
        self.assertIn("POL-TRAVEL-2027-V3-0", [x["document_id"] for x in future])
        self.assertNotIn("POL-TRAVEL-2026", [x["document_id"] for x in future])

    def test_09_graph_uses_explicit_business_relations(self):
        graph = FinanceGraphService(ROOT, self.db).query(self.session("U-EMP-003"), "PR-001")
        types = {x["type"] for x in graph["nodes"]}
        self.assertTrue({"vendor", "contract", "milestone", "payment_request", "invoice", "work_item"} <= types)
        self.assertTrue(graph["edges"])
        self.assertEqual(graph["source"], "sqlite_deterministic_relations")

    def test_10_enterprise_api_identity_and_no_legacy_chat_write(self):
        client = TestClient(app)
        session = client.post("/enterprise/session", json={"user_id": "U-EMP-001", "session_id": "api-enterprise-test"})
        self.assertEqual(session.status_code, 200)
        workspace = client.get("/enterprise/workspace", params={"session_id": "api-enterprise-test"})
        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(workspace.json()["identity"]["user_id"], "U-EMP-001")
        preview = client.post("/enterprise/expenses/precheck", json={
            "session_id": "api-enterprise-test", "city": "深圳", "days": 2, "lodging_price": "450"
        })
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["total_amount"], "910.00")
        self.assertEqual(preview.json()["next_step"]["assignee_name"], "王衡")
        response = client.post("/enterprise/assistant", json={
            "session_id": "api-enterprise-test", "question": "帮我报销深圳出差3天，经理级，住宿一晚550"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["level"], "普通员工")
        self.assertNotEqual(response.json()["status"], "confirmation_required")
        denied = client.get("/enterprise/admin-data", params={"session_id": "api-enterprise-test"})
        self.assertEqual(denied.status_code, 403)

    def test_11_submit_new_payment_uses_precheck_contract(self):
        created = self.workflow.submit_payment(self.session("U-EMP-003"), "PR-019")
        self.assertEqual(created["status"], "pending_department")
        self.assertEqual(created["current_assignee"], "U-MGR-SCM")
        self.assertTrue(created["requires_finance_manager"])
        self.assertEqual(created["exceptions"], [])
        with self.assertRaises(WorkflowConflictError):
            self.workflow.submit_payment(self.session("U-EMP-003"), "PR-019")

    def test_12_enterprise_assistant_uses_work_item_scope_for_legacy_payment(self):
        platform = UnifiedFinancePlatform(ROOT, self.db)
        owner = platform.handle_enterprise("请预检付款申请 PR-002", self.session("U-EMP-003"))
        self.assertIn("missing_acceptance", [x["code"] for x in owner["data"]["findings"]])
        with self.assertRaises(PermissionError):
            platform.handle_enterprise("请预检付款申请 PR-002", self.session("U-EMP-001"))

    def test_13_connection_context_closes_database(self):
        conn = connect(self.db)
        with conn:
            self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
