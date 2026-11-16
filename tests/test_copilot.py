# -*- coding: utf-8 -*-
"""财务 AI 助手（Copilot）离线测试：指标口径、阈值、权限、上下文隔离、报告、只读。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ["FINANCE_OFFLINE_DEMO"] = "true"
os.environ["FINANCE_RETRIEVAL_MODE"] = "lexical"

from finance_platform.copilot import FinanceCopilot, _pct  # noqa: E402
from finance_platform.database import build_database, connect  # noqa: E402
from finance_platform.enterprise_database import EnterpriseRepository  # noqa: E402
from finance_platform.ingestion import ROOT  # noqa: E402


class CopilotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "copilot.db"
        build_database(ROOT, self.db, rebuild=True)
        self.repo = EnterpriseRepository(self.db)
        self.copilot = FinanceCopilot(ROOT, self.db)
        self.sessions = {}

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, user_id: str) -> str:
        if user_id not in self.sessions:
            self.sessions[user_id] = self.repo.create_session(user_id)["session_id"]
        return self.sessions[user_id]

    # 1) 预算执行率/占用率口径：与数据库逐行核对（Decimal 精确计算）
    def test_budget_metrics_decimal(self):
        sid = self.session("U-ACCOUNTANT")
        result = self.copilot.try_handle("分析各部门预算执行情况", sid)
        self.assertEqual(result["scenario"], "copilot_budget")
        with connect(self.db) as conn:
            raw = conn.execute(
                "SELECT department, project_id, annual_budget, actual, committed, frozen "
                "FROM budgets WHERE fiscal_year=2026"
            ).fetchall()
        detail = result["data"]["detail"]["rows"]
        for r in raw:
            label = f"{r['department']}·{r['project_id']}"
            row = next(x for x in detail if x[0] == label)
            annual = Decimal(str(r["annual_budget"]))
            expected_exec = _pct(Decimal(str(r["actual"])), annual)
            expected_occ = _pct(Decimal(str(r["actual"])) + Decimal(str(r["committed"])) + Decimal(str(r["frozen"])), annual)
            self.assertEqual(row[5], str(expected_exec), label)
            self.assertEqual(row[6], str(expected_occ), label)

    # 2) 阈值标红：把阈值调到 10%，被标红的项目数应等于执行率>10% 的数量
    def test_threshold_flagging(self):
        sid = self.session("U-ACCOUNTANT")
        result = self.copilot.try_handle("分析各部门预算执行情况，执行率超过10%的标红并生成图表", sid)
        self.assertIn("超过阈值 10.00%", result["answer"])
        flagged = sum(1 for x in result["data"]["detail"]["rows"] if x[7] == "是")
        with connect(self.db) as conn:
            raw = conn.execute(
                "SELECT annual_budget, actual FROM budgets WHERE fiscal_year=2026"
            ).fetchall()
        expected = sum(1 for a, act in raw if _pct(Decimal(str(act)), Decimal(str(a))) > Decimal("10"))
        self.assertEqual(flagged, expected)
        self.assertTrue(any(x[7] == "是" for x in result["data"]["detail"]["rows"]))
        self.assertIn("#d64541", result["data"]["chart"]["svg"])

    # 3) 权限拦截：普通员工不能看跨部门预算
    def test_employee_forbidden(self):
        sid = self.session("U-EMP-001")
        with self.assertRaises(PermissionError):
            self.copilot.try_handle("分析各部门预算执行情况", sid)

    # 4) 连续追问：同会话继承阈值，新会话不串上下文
    def test_followup_context_and_isolation(self):
        sid = self.session("U-ACCOUNTANT")
        self.copilot.try_handle("分析各部门预算执行情况，执行率超过85%的标红并生成图表", sid)
        follow = self.copilot.try_handle("再看一下预算占用率", sid)
        self.assertIn("占用率", follow["answer"])
        self.assertIn("85.00%", follow["answer"])  # 阈值从上下文继承
        fresh_sid = self.repo.create_session("U-ACCOUNTANT")["session_id"]
        fresh = self.copilot.try_handle("再看一下预算占用率", fresh_sid)
        self.assertNotIn("85.00%", fresh["answer"])
        self.assertNotIn("超过阈值", fresh["answer"])

    # 5) 报告内容完整
    def test_report_content(self):
        sid = self.session("U-ACCOUNTANT")
        result = self.copilot.try_handle("分析各部门预算执行情况并生成图表", sid)
        report = result["data"].get("report_html", "")
        for kw in ["指标口径", "管理摘要", "数据明细", "来源表", "执行模式", "合成", "原始问题"]:
            self.assertIn(kw, report)
        self.assertIn("budgets", report)

    # 6) 只读：跑完分析类问题，业务表行数不变
    def test_readonly(self):
        sid = self.session("U-ACCOUNTANT")
        tables = ["work_items", "payment_requests", "reconciliation_runs",
                  "bank_transactions", "ledger_entries", "expense_reports"]
        with connect(self.db) as conn:
            before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        self.copilot.try_handle("统计各业务场景的事项数量和金额，按金额降序", sid)
        self.copilot.try_handle("生成银行对账差异分析报告", sid)
        self.copilot.try_handle("预审付款申请 PR-002 并生成风险报告", sid)
        self.copilot.try_handle("分析各部门预算执行情况", sid)
        with connect(self.db) as conn:
            after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        self.assertEqual(before, after)

    # 7) 各场景识别
    def test_scenarios_recognition(self):
        acc = self.session("U-ACCOUNTANT")
        emp = self.session("U-EMP-001")
        cases = [
            (acc, "统计各业务场景的事项数量和金额，按金额降序", "copilot_scenarios"),
            (acc, "生成银行对账差异分析报告", "copilot_recon"),
            (acc, "预审付款申请 PR-002 并生成风险报告", "copilot_precheck"),
            (emp, "我下周去上海出差三天，酒店每晚600元，检查是否超标并告诉我怎么处理", "copilot_travel"),
        ]
        for sid, q, expected in cases:
            result = self.copilot.try_handle(q, sid)
            self.assertEqual(result["scenario"], expected, q)
            self.assertIn("离线规则解析", result["data"]["execution_mode"])

    # 8) 旧问题不被 Copilot 劫持
    def test_old_questions_fallthrough(self):
        emp = self.session("U-EMP-001")
        self.assertIsNone(self.copilot.try_handle("请预检付款申请 PR-002", emp))
        self.assertIsNone(self.copilot.try_handle("普通员工在一类城市的住宿费用上限是多少", emp))
        self.assertIsNone(self.copilot.try_handle("执行对账分析 CNY-OPER-002", emp))

    # 9) 差旅超标：600/晚 对普通员工上海上限 500，应提示超标 200
    def test_travel_overlimit(self):
        emp = self.session("U-EMP-001")
        result = self.copilot.try_handle(
            "我下周去上海出差三天，酒店每晚600元，检查是否超标并告诉我怎么处理", emp)
        self.assertIn("500.00", result["answer"])
        self.assertIn("超标", result["answer"])
        self.assertEqual(result["data"]["detail"]["rows"][-1][3], "超标")


from decimal import Decimal  # noqa: E402


if __name__ == "__main__":
    unittest.main()
