from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ["FINANCE_OFFLINE_DEMO"] = "true"
os.environ["FINANCE_RETRIEVAL_MODE"] = "lexical"

from finance_platform.agent import FinanceAgent
from finance_platform.audit_trail import AgentAuditTrail
from finance_platform.database import build_database, connect
from finance_platform.enterprise_database import EnterpriseRepository
from finance_platform.ingestion import ROOT
from finance_platform.number_guard import validate_answer_numbers
from finance_platform.orchestrator import UnifiedFinancePlatform
from finance_platform.policy_search import PolicySearch
from test_agent import MockLLM, final, tool_call


class GuardAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "guard-audit.db"
        build_database(ROOT, self.db, rebuild=True)
        self.repo = EnterpriseRepository(self.db)
        self.sessions = {}

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, user_id):
        if user_id not in self.sessions:
            self.sessions[user_id] = self.repo.create_session(user_id)["session_id"]
        return self.sessions[user_id]

    def test_numeric_guard_formats_chinese_numbers_and_rejects_unknown(self):
        evidence = ["住宿限额 500.00 元，预付款比例为百分之三十。"]
        self.assertTrue(validate_answer_numbers("[1] 住宿限额 500 元，预付款上限为百分之三十。", evidence).valid)
        validation = validate_answer_numbers("合计 999.00 元", evidence)
        self.assertFalse(validation.valid)
        self.assertEqual(validation.missing, ("999",))

    def test_agent_blocks_unverified_number_and_persists_tool_audit(self):
        llm = MockLLM([
            tool_call("analyze_budget", {"metric": "execution"}),
            final("共有 999 个预算项目。"),
        ])
        sid = self.session("U-ACCOUNTANT")
        result = FinanceAgent(ROOT, self.db, llm=llm).try_handle("分析预算项目", sid)
        self.assertEqual(result["status"], "guard_blocked")
        self.assertIn("999", result["data"]["numeric_guard"]["missing"])
        events = AgentAuditTrail(self.db).list_events()
        self.assertEqual({event["tool_name"] for event in events}, {"analyze_budget", "assistant_final"})
        self.assertTrue(all(event["question"] == "分析预算项目" for event in events))
        self.assertTrue(all(event["user_id"] == "U-ACCOUNTANT" for event in events))

    def test_prepare_expense_is_read_only_and_returns_structured_draft(self):
        llm = MockLLM([
            tool_call("prepare_expense", {"city": "深圳", "days": 2, "lodging_price": 450}),
            final("已生成深圳 2 天报销草稿，合计 910 元，请核对后确认。"),
        ])
        sid = self.session("U-EMP-001")
        with connect(self.db) as conn:
            before = conn.execute("SELECT COUNT(*) FROM expense_reports").fetchone()[0]
        result = FinanceAgent(ROOT, self.db, llm=llm).try_handle("帮我报销深圳出差2天住宿450元", sid)
        self.assertEqual(result["pending_action"]["type"], "expense_draft")
        self.assertEqual(result["pending_action"]["payload"]["city"], "深圳")
        self.assertTrue(result["human_confirmation_required"])
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM expense_reports").fetchone()[0], before)
        self.assertIn("prepare_expense", {event["tool_name"] for event in AgentAuditTrail(self.db).list_events()})
        fallback_llm = MockLLM([
            tool_call("prepare_expense", {"city": "深圳", "days": 2, "lodging_price": 450}),
            final("合计 1000 元。"),
        ])
        guarded = FinanceAgent(ROOT, self.db, llm=fallback_llm).try_handle("帮我报销深圳出差2天住宿450元", sid)
        self.assertEqual(guarded["status"], "guard_blocked")
        self.assertIn("910.00", guarded["answer"])
        self.assertNotIn("1000", guarded["answer"])

    def test_vector_failure_returns_explicit_lexical_fallback(self):
        search = PolicySearch(ROOT, self.db)
        search.mode = "vector"

        class BrokenVector:
            def search(self, *args, **kwargs):
                raise RuntimeError("向量服务不可用")

        search._vector = BrokenVector()
        result = search.answer("普通员工住宿上限", "employee")
        self.assertEqual(result["retrieval_mode"], "lexical_fallback")
        self.assertIn("向量服务不可用", result["fallback_reason"])
        self.assertEqual(result["generation_mode"], "extractive")

    def test_offline_prepare_action_and_audit_survive_reopen(self):
        sid = self.session("U-EMP-001")
        result = UnifiedFinancePlatform(ROOT, self.db).handle_enterprise("帮我报销深圳出差2天，住宿450元", sid)
        self.assertEqual(result["pending_action"]["payload"]["days"], 2)
        self.assertEqual(result["pending_action"]["preview"]["total_amount"], "910.00")
        events = AgentAuditTrail(self.db).list_events(tool_name="prepare_action")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["question"], "帮我报销深圳出差2天，住宿450元")
        self.assertEqual(AgentAuditTrail(self.db).count(), 1)


if __name__ == "__main__":
    unittest.main()
