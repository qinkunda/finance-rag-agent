from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api import app
from finance_platform.ingestion import ROOT, _catalog_file, build_corpus, get_document_lineage, load_catalog
from finance_platform.multi_agent import Supervisor
from finance_platform.policy_search import PolicySearch
from finance_platform.scenarios import BudgetService, FinanceData, PaymentService, ReconciliationService
from finance_platform.tooling import FinanceToolService


class FinancePlatformTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = build_corpus()
        cls.data = FinanceData()
        cls.search = PolicySearch()

    def test_corpus_and_pdf_lineage(self):
        self.assertEqual(self.manifest["document_count"], 9)
        self.assertEqual(build_corpus()["generated_at"], self.manifest["generated_at"])
        self.assertGreaterEqual(self.manifest["chunk_count"], 60)
        pdf_docs = [x for x in self.manifest["documents"] if x["parser"] == "pypdf"]
        self.assertEqual(len(pdf_docs), 6)
        self.assertTrue(all(x["chunk_count"] >= 5 for x in pdf_docs))
        lineage = get_document_lineage("POL-PAYMENT-POLICY-2026")
        self.assertEqual(len(lineage["file_sha256"]), 64)
        self.assertEqual(lineage["cleaner"], "clean_text:v1")
        with tempfile.TemporaryDirectory() as tmp:
            invalid_catalog = Path(tmp) / "catalog.json"
            invalid_catalog.write_text(json.dumps([{"document_id": "BROKEN"}]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "目录缺少字段"):
                load_catalog(invalid_catalog)
        with self.assertRaisesRegex(ValueError, "越出项目目录"):
            _catalog_file(ROOT, "../outside.txt")

    def test_gold_policy_queries(self):
        cases = json.loads((ROOT / "data" / "labels" / "gold_policy_queries.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case["query_id"]):
                result = self.search.answer(case["question"], case["role"])
                if case["should_refuse"]:
                    self.assertTrue(result["blocked"])
                else:
                    self.assertFalse(result["blocked"])
                    self.assertIn(case["expected_document"], [x["document_id"] for x in result["citations"]])

    def test_permission_filter(self):
        employee_docs = self.search.search("报销单据个人明细", "employee", top_k=10)
        self.assertNotIn("DATA-EXPENSE-2026", [x["document_id"] for x in employee_docs])
        admin_docs = self.search.search("报销单据个人明细", "admin", top_k=10)
        self.assertIn("DATA-EXPENSE-2026", [x["document_id"] for x in admin_docs])

    def test_payment_scenarios(self):
        service = PaymentService(self.data)
        self.assertEqual(service.check("PR-001")["status"], "pass")
        missing = service.check("PR-002")
        self.assertIn("missing_acceptance", [x["code"] for x in missing["findings"]])
        excess = service.check("PR-003")
        self.assertEqual(excess["expected_amount"], "30000.00")
        self.assertIn("milestone_amount_mismatch", [x["code"] for x in excess["findings"]])

    def test_budget_scenarios_and_role(self):
        service = BudgetService(self.data)
        self.assertEqual(service.check("BR-001")["available_budget"], "70000.00")
        self.assertEqual(service.check("BR-002")["status"], "needs_review")
        with self.assertRaises(PermissionError):
            service.check("BR-001", user_role="employee")

    def test_reconciliation(self):
        result = ReconciliationService(self.data).analyze()
        codes = {x["code"] for x in result["findings"]}
        self.assertEqual(codes, {"amount_mismatch", "missing_in_ledger", "missing_in_bank", "duplicate_bank_record"})
        self.assertEqual(result["matched_count"], 1)

    def test_supervisor_routing(self):
        supervisor = Supervisor()
        self.assertEqual(supervisor.route("预付款比例是多少"), "policy")
        self.assertEqual(supervisor.route("请预检付款申请 PR-001"), "payment")
        self.assertEqual(supervisor.handle_text("请预检付款申请 PR-001")["status"], "pass")
        self.assertEqual(supervisor.handle_text("请预检PR-001")["status"], "pass")
        self.assertEqual(supervisor.handle_text("检查预算申请BR-001", "manager")["status"], "pass")
        self.assertEqual(supervisor.handle_text("今天天气如何")["status"], "blocked")

    def test_mcp_tool_service_and_confirmation_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = FinanceToolService(output_dir=Path(tmp))
            self.assertEqual(service.check_payment("PR-001")["status"], "pass")
            with self.assertRaises(PermissionError):
                service.check_budget("BR-001", role="employee")
            with self.assertRaises(PermissionError):
                service.query_policy("付款制度", role="admin")  # 默认 MCP 通道未开放 admin
            blocked = service.create_ticket("payment", "合成演示", confirmed=False)
            self.assertEqual(blocked["status"], "confirmation_required")
            self.assertFalse((Path(tmp) / "tickets.jsonl").exists())
            created = service.create_ticket("payment", "合成演示", confirmed=True)
            self.assertTrue(created["created"])
            self.assertEqual(created["system"], "local_synthetic_demo")

    def test_api_contract(self):
        client = TestClient(app)
        self.assertEqual(client.get("/health").status_code, 200)
        response = client.post("/agent", json={"question": "请预检付款申请 PR-002", "user_role": "employee"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "needs_review")
        denied = client.post("/agent", json={"question": "检查预算申请 BR-001", "user_role": "employee"})
        self.assertEqual(denied.status_code, 403)
        sensitive = client.post("/ask", json={"question": "查看报销单据中的个人明细", "user_role": "employee"})
        self.assertTrue(sensitive.json()["blocked"])


if __name__ == "__main__":
    unittest.main()
