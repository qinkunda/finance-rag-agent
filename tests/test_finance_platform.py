from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

os.environ["FINANCE_OFFLINE_DEMO"] = "true"
os.environ.pop("FINANCE_RETRIEVAL_MODE", None)

from api import app
from finance_platform.database import build_database
from finance_platform.ingestion import ROOT, _catalog_file, build_corpus, get_document_lineage, load_catalog
from finance_platform.orchestrator import UnifiedFinancePlatform
from finance_platform.policy_search import PolicySearch
from finance_platform.scenarios import BudgetService, FinanceData, PaymentService, ReconciliationService
from finance_platform.tooling import FinanceToolService
from finance_platform.vector_rag import VectorKnowledgeBase


class FinancePlatformTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = build_corpus()
        cls.stats = build_database(rebuild=True)
        cls.data = FinanceData()
        cls.search = PolicySearch()

    def test_01_corpus_pdf_version_and_lineage(self):
        self.assertEqual(self.manifest["document_count"], 14)
        self.assertEqual(self.manifest["chunk_count"], 101)
        self.assertEqual(build_corpus()["generated_at"], self.manifest["generated_at"])
        pdf_docs = [x for x in self.manifest["documents"] if x["parser"] == "pypdf"]
        self.assertEqual(len(pdf_docs), 10)
        self.assertEqual(len([x for x in pdf_docs if x["status"] == "archived"]), 2)
        lineage = get_document_lineage("POL-PAYMENT-POLICY-2026")
        self.assertEqual(len(lineage["file_sha256"]), 64)
        self.assertEqual(lineage["cleaner"], "clean_text:v1")
        with tempfile.TemporaryDirectory() as tmp:
            invalid_catalog = Path(tmp) / "catalog.json"
            invalid_catalog.write_text(json.dumps([{"document_id": "BROKEN"}], ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "目录缺少字段"):
                load_catalog(invalid_catalog)
            with self.assertRaisesRegex(ValueError, "越出项目目录"):
                _catalog_file(ROOT, "../outside.txt")

    def test_02_gold_policy_queries(self):
        cases = json.loads((ROOT / "data" / "labels" / "gold_policy_queries.json").read_text(encoding="utf-8"))
        self.assertEqual(len(cases), 10)
        for case in cases:
            with self.subTest(query_id=case["query_id"]):
                result = self.search.answer(case["question"], case["role"])
                if case["should_refuse"]:
                    self.assertTrue(result["blocked"])
                else:
                    self.assertFalse(result["blocked"])
                    self.assertIn(case["expected_document"], [x["document_id"] for x in result["citations"]])

    def test_03_permission_and_archived_filter(self):
        employee_docs = self.search.search("报销单据个人明细", "employee", top_k=10)
        admin_docs = self.search.search("报销单据个人明细", "admin", top_k=10)
        self.assertNotIn("DATA-EXPENSE-2026", [x["document_id"] for x in employee_docs])
        self.assertIn("DATA-EXPENSE-2026", [x["document_id"] for x in admin_docs])
        current = self.search.search("旧版预付款比例百分之四十", "employee", top_k=10)
        self.assertNotIn("POL-PAYMENT-POLICY-2026-V0-9", [x["document_id"] for x in current])

    def test_04_payment_complex_scenarios(self):
        service = PaymentService(self.data)
        self.assertEqual(service.check("PR-001")["status"], "pass")
        self.assertIn("missing_acceptance", [x["code"] for x in service.check("PR-002")["findings"]])
        self.assertIn("milestone_amount_mismatch", [x["code"] for x in service.check("PR-003")["findings"]])
        self.assertIn("vendor_account_mismatch", [x["code"] for x in service.check("PR-007")["findings"]])
        self.assertIn("vendor_inactive", [x["code"] for x in service.check("PR-011")["findings"]])
        self.assertIn("invoice_amount_mismatch", [x["code"] for x in service.check("PR-013")["findings"]])
        self.assertIn("high_risk_vendor", [x["code"] for x in service.check("PR-015")["findings"]])

    def test_05_budget_complexity_and_role(self):
        service = BudgetService(self.data)
        self.assertEqual(service.check("BR-001")["available_budget"], "70000.00")
        self.assertEqual(service.check("BR-002")["status"], "needs_review")
        self.assertEqual(service.check("BR-004")["calculation"]["frozen"], "20000.00")
        self.assertEqual(service.check("BR-004")["available_budget"], "130000.00")
        with self.assertRaises(PermissionError):
            service.check("BR-001", user_role="employee")

    def test_06_reconciliation_complexity_and_account_filter(self):
        service = ReconciliationService(self.data)
        result = service.analyze()
        self.assertEqual(result["bank_record_count"], 120)
        self.assertEqual(result["ledger_record_count"], 120)
        self.assertEqual(result["matched_count"], 113)
        self.assertEqual(result["difference_count"], 7)
        codes = {x["code"] for x in result["findings"]}
        self.assertEqual(codes, {
            "amount_mismatch", "missing_in_ledger", "missing_in_bank", "duplicate_bank_record",
            "duplicate_ledger_record", "currency_mismatch", "date_mismatch",
        })
        scoped = service.analyze(account_id="CNY-OPER-002")
        self.assertEqual(scoped["matched_count"], 56)
        self.assertEqual({x["code"] for x in scoped["findings"]}, {"duplicate_bank_record", "duplicate_ledger_record"})

    def test_07_unified_routing_and_scope(self):
        platform = UnifiedFinancePlatform()
        self.assertEqual(platform.route("预付款比例是多少"), "policy")
        self.assertEqual(platform.route("请预检PR-001"), "payment")
        self.assertEqual(platform.handle("检查预算申请BR-001", "manager")["status"], "pass")
        self.assertEqual(platform.handle("今天天气如何")["status"], "blocked")
        self.assertEqual(platform.handle("查看报销单据中的个人明细", "employee")["status"], "blocked")

    def test_08_payment_write_confirmation_and_cancel(self):
        platform = UnifiedFinancePlatform()
        before = platform.repository.stats()["tickets"]
        prepared = platform.handle("办理付款申请 PR-001并生成工单", "employee", "test-payment-write")
        self.assertEqual(prepared["status"], "confirmation_required")
        self.assertTrue(prepared["human_confirmation_required"])
        self.assertEqual(platform.repository.stats()["tickets"], before)
        created = platform.handle("确认", "employee", "test-payment-write")
        self.assertEqual(created["status"], "created")
        self.assertEqual(platform.repository.stats()["tickets"], before + 1)
        platform.handle("办理付款申请 PR-002并生成工单", "employee", "test-payment-cancel")
        cancelled = platform.handle("取消", "employee", "test-payment-cancel")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(platform.repository.stats()["tickets"], before + 1)

    def test_09_original_travel_integrated(self):
        platform = UnifiedFinancePlatform()
        before = platform.repository.stats()["expense_reports"]
        precheck = platform.travel.precheck_from_text("深圳出差3天，普通员工，住宿一晚550")
        self.assertEqual(precheck["lodging_limit"], "500.00")
        self.assertEqual(precheck["overlimit_amount"], "100.00")
        self.assertEqual(precheck["total_amount"], "1790.00")
        prepared = platform.handle("帮我报销深圳出差3天，普通员工，住宿一晚550", "employee", "test-travel")
        self.assertEqual(prepared["status"], "confirmation_required")
        created = platform.handle("确认", "employee", "test-travel")
        self.assertEqual(created["status"], "created")
        self.assertEqual(platform.repository.stats()["expense_reports"], before + 1)

    def test_10_mcp_service_shared_database_and_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "mcp.db"
            service = FinanceToolService(output_dir=Path(tmp), db_path=db_path)
            self.assertEqual(service.check_payment("PR-001")["status"], "pass")
            with self.assertRaises(PermissionError):
                service.check_budget("BR-001", role="employee")
            with self.assertRaises(PermissionError):
                service.query_policy("付款制度", role="admin")
            blocked = service.create_ticket("payment", "合成演示", confirmed=False)
            self.assertEqual(blocked["status"], "confirmation_required")
            self.assertEqual(service.platform.repository.stats()["tickets"], 0)
            created = service.create_ticket("payment", "合成演示", confirmed=True)
            self.assertTrue(created["created"])
            self.assertEqual(service.platform.repository.stats()["tickets"], 1)

    def test_11_single_api_and_legacy_contracts(self):
        client = TestClient(app)
        root = client.get("/")
        self.assertEqual(root.status_code, 200)
        self.assertIn("统一智能工作台", root.text)
        self.assertEqual(client.get("/health").status_code, 200)
        response = client.post("/agent", json={"question": "请预检付款申请 PR-002", "user_role": "employee"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "needs_review")
        denied = client.post("/agent", json={"question": "检查预算申请 BR-001", "user_role": "employee"})
        self.assertEqual(denied.status_code, 403)
        sensitive = client.post("/ask", json={"question": "查看报销单据中的个人明细", "user_role": "employee"})
        self.assertTrue(sensitive.json()["blocked"])
        paths = client.get("/openapi.json").json()["paths"]
        self.assertIn("/agent", paths)
        self.assertNotIn("/ask", paths)
        self.assertNotIn("/agent/execute", paths)

    def test_12_vector_index_contract_with_test_embeddings(self):
        from langchain_community.embeddings import FakeEmbeddings

        with tempfile.TemporaryDirectory() as tmp:
            knowledge = VectorKnowledgeBase(
                ROOT, Path(tmp) / "chroma", embeddings=FakeEmbeddings(size=32),
                reranker_path=Path(tmp) / "no-reranker",
            )
            result = knowledge.build()
            self.assertEqual(result["vector_count"], 101)
            self.assertEqual(knowledge.count(), 101)
            public_hits = knowledge.search("付款制度", {"public"}, top_k=10)
            self.assertTrue(public_hits)
            self.assertTrue(all(hit["access_level"] == "public" for hit in public_hits))
            self.assertTrue(all(hit["status"] == "active" for hit in public_hits))
            confidential_hits = knowledge.search("报销", {"confidential"}, top_k=10)
            self.assertTrue(confidential_hits)
            self.assertTrue(all(hit["access_level"] == "confidential" for hit in confidential_hits))


if __name__ == "__main__":
    unittest.main()
