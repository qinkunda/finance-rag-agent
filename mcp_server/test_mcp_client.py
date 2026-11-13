"""协议级离线验收：真实启动 MCP 子进程，不调用外部模型或企业系统。"""
import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

ROOT = Path(__file__).resolve().parents[1]
SERVER = Path(__file__).with_name("finance_server.py")


async def main() -> None:
    # MCP 默认只继承白名单环境变量；显式转发 PYTHONPATH，支持隔离依赖目录验收。
    env = get_default_environment()
    if python_path := os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] = python_path
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER)],
        cwd=str(ROOT),
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print("TOOLS", names)
            expected_tools = {
                "query_finance_policy",
                "check_payment_request",
                "check_budget_request",
                "analyze_reconciliation",
                "get_document_lineage",
                "create_finance_ticket",
            }
            assert expected_tools.issubset(names)
            cases = [
                ("query_finance_policy", {"question": "预付款最多可以付多少比例？", "user_role": "employee"}),
                ("check_payment_request", {"request_id": "PR-001", "user_role": "employee"}),
                ("check_budget_request", {"request_id": "BR-001", "user_role": "manager"}),
                ("analyze_reconciliation", {"user_role": "manager"}),
                ("get_document_lineage", {"document_id": "POL-PAYMENT-POLICY-2026", "user_role": "employee"}),
                ("create_finance_ticket", {"scenario": "payment", "summary": "PR-001 合成演示工单", "confirmed": False, "user_role": "employee"}),
            ]
            results = {}
            for name, arguments in cases:
                result = await session.call_tool(name, arguments)
                print(name, "isError=", result.isError)
                assert not result.isError
                payload = result.structuredContent
                assert isinstance(payload, dict)
                results[name] = payload
                print(payload)

            assert results["query_finance_policy"]["blocked"] is False
            assert results["query_finance_policy"]["citations"]
            assert results["check_payment_request"]["status"] == "pass"
            assert results["check_budget_request"]["available_budget"] == "70000.00"
            assert results["analyze_reconciliation"]["difference_count"] == 4
            assert len(results["get_document_lineage"]["file_sha256"]) == 64
            ticket = results["create_finance_ticket"]
            assert ticket["status"] == "confirmation_required" and ticket["created"] is False
            print("MCP_PROTOCOL_TEST_OK: 6 tools discovered; 6 calls returned expected results.")


if __name__ == "__main__":
    asyncio.run(main())
