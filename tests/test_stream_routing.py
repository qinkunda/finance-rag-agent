from __future__ import annotations

import json
import os
import unittest
import uuid

os.environ["FINANCE_OFFLINE_DEMO"] = "true"
os.environ["FINANCE_RETRIEVAL_MODE"] = "lexical"
os.environ.pop("FINANCE_AGENT_ENABLED", None)

from fastapi.testclient import TestClient
from api import app, get_agent
from finance_platform.llm_routing import model_for_question


class StreamRoutingTest(unittest.TestCase):
    def setUp(self):
        get_agent.cache_clear()
        self.client = TestClient(app)
        self.suffix = uuid.uuid4().hex[:10]

    def session(self, user_id):
        session_id = f"stream-{user_id}-{self.suffix}"
        response = self.client.post("/enterprise/session", json={"user_id": user_id, "session_id": session_id})
        self.assertEqual(response.status_code, 200)
        return session_id

    def stream(self, session_id, question):
        with self.client.stream("POST", "/enterprise/assistant/stream",
                                json={"session_id": session_id, "question": question}) as response:
            self.assertEqual(response.status_code, 200)
            text = "".join(response.iter_text())
        return [json.loads(line[5:].strip()) for line in text.splitlines() if line.startswith("data:")]

    def test_model_routing_simple_complex_and_disabled(self):
        os.environ["FINANCE_MODEL_ROUTING_ENABLED"] = "true"
        os.environ["FINANCE_LLM_SMALL_MODEL"] = "qwen-turbo"
        os.environ["FINANCE_LLM_MODEL"] = "qwen-plus"
        self.assertEqual(model_for_question("住宿上限是多少"), "qwen-turbo")
        self.assertEqual(model_for_question("分析各部门预算执行情况并生成管理层报告"), "qwen-plus")
        os.environ["FINANCE_MODEL_ROUTING_ENABLED"] = "false"
        self.assertEqual(model_for_question("住宿上限是多少"), "qwen-plus")
        os.environ.pop("FINANCE_MODEL_ROUTING_ENABLED", None)

    def test_stream_returns_progress_delta_and_done(self):
        sid = self.session("U-EMP-001")
        events = self.stream(sid, "普通员工在一类城市的住宿费用上限是多少")
        types = {event["type"] for event in events}
        self.assertTrue({"start", "delta", "done"} <= types)
        result = next(event["result"] for event in events if event["type"] == "done")
        self.assertIn("500", result["answer"])

    def test_stream_prepare_action_does_not_create_expense(self):
        sid = self.session("U-EMP-001")
        before = self.client.get("/health").json()["database"]["expense_reports"]
        events = self.stream(sid, "帮我报销深圳出差2天，住宿450元")
        result = next(event["result"] for event in events if event["type"] == "done")
        self.assertEqual(result["pending_action"]["payload"]["city"], "深圳")
        self.assertEqual(result["pending_action"]["preview"]["total_amount"], "910.00")
        self.assertEqual(self.client.get("/health").json()["database"]["expense_reports"], before)

    def test_audit_api_permission_and_question_persistence(self):
        employee = self.session("U-EMP-001")
        auditor = self.session("U-AUDITOR")
        self.stream(employee, "帮我报销深圳出差2天，住宿450元")
        events = self.client.get("/enterprise/audit-events", params={"session_id": auditor, "tool_name": "prepare_action"})
        self.assertEqual(events.status_code, 200)
        self.assertTrue(any("深圳出差2天" in event["question"] for event in events.json()))
        denied = self.client.get("/enterprise/audit-events", params={"session_id": employee})
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
