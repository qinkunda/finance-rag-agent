# -*- coding: utf-8 -*-
"""企业智能体（LLM tool-calling）测试：用 MockLLM 脚本化验证全链路。

覆盖：工具真实计算数据回灌 LLM、权限拦截在工具层生效并解释、制度检索引用、
无 LLM 时返回 None、多轮工具循环、前端画布数据完整性。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ["FINANCE_OFFLINE_DEMO"] = "true"
os.environ["FINANCE_RETRIEVAL_MODE"] = "lexical"

from finance_platform.agent import FinanceAgent  # noqa: E402
from finance_platform.database import build_database  # noqa: E402
from finance_platform.enterprise_database import EnterpriseRepository  # noqa: E402
from finance_platform.ingestion import ROOT  # noqa: E402


class MockLLM:
    """脚本化 LLM：按顺序吐出预设响应；记录每次收到的完整 messages。"""

    model = "mock-qwen"

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, temperature=0.1):
        self.calls.append([m.copy() for m in messages])
        if not self.script:
            return {"content": "（脚本耗尽）", "tool_calls": [], "finish_reason": "stop"}
        return self.script.pop(0)


def tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"content": None, "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }], "finish_reason": "tool_calls"}


def final(text: str) -> dict:
    return {"content": text, "tool_calls": [], "finish_reason": "stop"}


class FinanceAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "agent.db"
        build_database(ROOT, self.db, rebuild=True)
        self.repo = EnterpriseRepository(self.db)
        self.sessions = {}

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, user_id: str) -> str:
        if user_id not in self.sessions:
            self.sessions[user_id] = self.repo.create_session(user_id)["session_id"]
        return self.sessions[user_id]

    def make_agent(self, script: list) -> tuple[FinanceAgent, MockLLM]:
        llm = MockLLM(script)
        return FinanceAgent(ROOT, self.db, llm=llm), llm

    # 1) 预算分析：工具真实计算 → 数据回灌 LLM → 画布拿到图表
    def test_budget_toolcall_roundtrip(self):
        agent, llm = self.make_agent([
            tool_call("analyze_budget", {"metric": "execution", "threshold": 85}),
            final("结论：8 个预算项目均未超 85%，财务健康。"),
        ])
        sid = self.session("U-ACCOUNTANT")
        result = agent.try_handle("分析各部门预算执行情况", sid)
        self.assertEqual(result["scenario"], "agent_tool_calling")
        self.assertIn("未超 85%", result["answer"])
        self.assertIn("mock-qwen", result["data"]["execution_mode"])
        # 画布数据完整
        self.assertIn("svg", result["data"]["chart"])
        self.assertTrue(result["data"]["detail"]["rows"])
        # 第二次调用 LLM 时，messages 里应包含工具返回的真实计算值
        tool_msgs = [m for m in llm.calls[1] if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn("预算执行率", tool_msgs[0]["content"])
        self.assertIn("共 8 个预算项目", tool_msgs[0]["content"])

    # 2) 权限拦截下沉在工具层：员工问预算 → 工具返回权限错误 → 无可用数据时交回确定性服务（返回 None）
    def test_permission_enforced_inside_loop(self):
        agent, llm = self.make_agent([
            tool_call("analyze_budget", {"metric": "execution"}),
            final("抱歉，你没有跨部门预算分析权限。"),
        ])
        sid = self.session("U-EMP-001")
        result = agent.try_handle("分析各部门预算执行情况", sid)
        self.assertIsNone(result)  # 新设计：无工具成功调用时回退确定性服务（最终由规则引擎抛 403）
        tool_msgs = [m for m in llm.calls[1] if m["role"] == "tool"]
        self.assertTrue(tool_msgs[0]["content"].startswith("权限不足"))

    # 3) 制度检索：search_policy 返回条款引用
    def test_policy_search_with_citations(self):
        agent, llm = self.make_agent([
            tool_call("search_policy", {"query": "住宿费 上限"}),
            final("普通员工在一类城市住宿上限为 500 元/晚，依据《差旅费管理办法》。"),
        ])
        sid = self.session("U-EMP-001")
        result = agent.try_handle("普通员工在一类城市的住宿费用上限是多少", sid)
        tool_msgs = [m for m in llm.calls[1] if m["role"] == "tool"]
        self.assertIn("引用条款", tool_msgs[0]["content"])
        self.assertIn("《", tool_msgs[0]["content"])

    # 4) 多轮工具：先查制度再算差旅（真实值回灌）
    def test_multi_round_travel(self):
        agent, llm = self.make_agent([
            tool_call("search_policy", {"query": "上海 住宿 标准"}),
            tool_call("travel_precheck", {"city": "上海", "days": 3, "lodging_price": 600}, call_id="call_2"),
            final("住宿 600 元/晚超出上限 500 元，超标 200 元；建议调整酒店或走例外审批。"),
        ])
        sid = self.session("U-EMP-001")
        result = agent.try_handle("我下周去上海出差三天，酒店每晚600元，检查是否超标并告诉我怎么处理", sid)
        self.assertEqual(len(llm.calls), 3)
        second_round_tools = [m for m in llm.calls[2] if m["role"] == "tool"]
        self.assertTrue(any("500" in m["content"] for m in second_round_tools))
        self.assertIn("detail", result["data"])

    # 5) 无 LLM 配置时返回 None（离线兜底路径接管）
    def test_none_without_llm(self):
        agent = FinanceAgent(ROOT, self.db, llm=None)
        sid = self.session("U-EMP-001")
        self.assertIsNone(agent.try_handle("任意问题", sid))

    # 6) 未知道具调用与坏 JSON 参数不崩溃
    def test_unknown_tool_and_bad_args(self):
        agent, _ = self.make_agent([
            {"content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "drop_table", "arguments": "{}"}},
                {"id": "c2", "type": "function", "function": {"name": "analyze_budget", "arguments": "{bad json"}},
            ], "finish_reason": "tool_calls"},
            final("收到。"),
        ])
        sid = self.session("U-ACCOUNTANT")
        result = agent.try_handle("随便问", sid)
        self.assertEqual(result["status"], "completed")

    # 7) 会话记忆：同会话追问带历史；新会话隔离
    def test_session_memory(self):
        agent, llm = self.make_agent([
            tool_call("analyze_budget", {"metric": "execution", "threshold": 85}),
            final("第一次回答：全部未超 85%。"),
            final("第二次回答。"),
        ])
        sid = self.session("U-ACCOUNTANT")
        agent.try_handle("分析各部门预算执行情况，超过85%的标红", sid)   # 消耗 calls[0](工具) + calls[1](终答)
        agent.try_handle("那占用率呢", sid)                                # calls[2]：messages 必须带历史
        second_call = llm.calls[2]
        texts = [m["content"] for m in second_call if m["role"] in ("user", "assistant")]
        self.assertTrue(any("分析各部门预算执行情况" in (t or "") for t in texts))
        self.assertTrue(any("第一次回答" in (t or "") for t in texts))
        # 新会话不带历史
        fresh = self.repo.create_session("U-ACCOUNTANT")["session_id"]
        agent.try_handle("占用率呢", fresh)                                # calls[3]
        third_texts = [m["content"] for m in llm.calls[3] if m["role"] in ("user", "assistant")]
        self.assertFalse(any("分析各部门预算执行情况" in (t or "") for t in third_texts))

    # 8) 有图表的智能体结果自动生成可下载报告
    def test_report_auto_generated(self):
        agent, _ = self.make_agent([
            tool_call("analyze_budget", {"metric": "execution"}),
            final("结论：执行率整体健康。"),
        ])
        sid = self.session("U-ACCOUNTANT")
        result = agent.try_handle("分析各部门预算执行情况", sid)
        report = result["data"].get("report_html", "")
        for kw in ["指标口径", "管理摘要", "数据明细", "合成", "原始问题"]:
            self.assertIn(kw, report)


class ModelFallbackTest(unittest.TestCase):
    """模型降级链：主模型不可用（quota 类）自动降下一档；不可恢复错误直接抛。"""

    def _make(self, outcomes: dict[str, Exception | dict]):
        from finance_platform.agent.llm import OpenAICompatibleLLM
        llm = OpenAICompatibleLLM(api_key="test", model="model-a",
                                  fallback_models=["model-b", "model-c"])
        calls: list[str] = []

        def fake_once(messages, tools, temperature):
            calls.append(llm.model)
            outcome = outcomes[llm.model]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        llm._chat_once = fake_once  # type: ignore[assignment]
        return llm, calls

    def test_fallback_to_second_model(self):
        from finance_platform.agent.llm import LLMError
        llm, calls = self._make({
            "model-a": LLMError("AllocationQuota.FreeTierOnly quota exhausted"),
            "model-b": {"content": "ok", "tool_calls": []},
        })
        result = llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(result["content"], "ok")
        self.assertEqual(result["actual_model"], "model-b")
        self.assertEqual(result["fallback_from"], "model-a")
        self.assertEqual(llm.model, "model-b")  # 后续轮次固定用降级后的模型
        self.assertIn("model-a", llm.last_error)

    def test_non_recoverable_error_no_fallback(self):
        from finance_platform.agent.llm import LLMError
        llm, calls = self._make({"model-a": LLMError("401 Unauthorized Invalid API-key")})
        with self.assertRaises(LLMError):
            llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(calls, ["model-a"])  # 鉴权失败不浪费降级尝试

    def test_fallback_chain_exhausted(self):
        from finance_platform.agent.llm import LLMError
        llm, calls = self._make({m: LLMError("quota exhausted") for m in ["model-a", "model-b", "model-c"]})
        with self.assertRaises(LLMError):
            llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(calls, ["model-a", "model-b", "model-c"])

    def test_chain_dedup(self):
        from finance_platform.agent.llm import OpenAICompatibleLLM
        llm = OpenAICompatibleLLM(api_key="t", model="qwen-x",
                                  fallback_models=["qwen-x", "qwen-y", "qwen-y"])
        self.assertEqual(llm.fallback_models, ["qwen-y"])


if __name__ == "__main__":
    unittest.main()
