"""财务知识工程 MCP Server；stdio 的 stdout 仅用于 MCP 协议。"""
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP
from finance_platform.tooling import FinanceToolService

mcp = FastMCP("finance-multiscenario")
service = FinanceToolService(ROOT)


@mcp.tool()
def query_finance_policy(question: str, user_role: str = "employee") -> dict[str, Any]:
    """检索差旅、付款、预算、对账、发票等合成财务制度，返回答案与条款引用。只读。"""
    return service.query_policy(question, user_role)


@mcp.tool()
def check_payment_request(request_id: str, user_role: str = "employee") -> dict[str, Any]:
    """按合同里程碑、发票、验收、供应商账户和金额执行付款预检，例如 PR-001。只读。"""
    return service.check_payment(request_id, user_role)


@mcp.tool()
def check_budget_request(request_id: str, user_role: str = "manager") -> dict[str, Any]:
    """计算项目可用预算并预检申请，例如 BR-001；仅 manager 通道。只读。"""
    return service.check_budget(request_id, user_role)


@mcp.tool()
def analyze_reconciliation(user_role: str = "manager") -> dict[str, Any]:
    """分析合成银行流水和总账，识别金额差异、缺失、未达及重复记录；仅 manager 通道。"""
    return service.analyze_reconciliation(user_role)


@mcp.tool()
def get_document_lineage(document_id: str, user_role: str = "employee") -> dict[str, Any]:
    """查询文档文件哈希、解析器、清洗器、切分器和 Chunk 数量，受文档权限约束。"""
    return service.lineage(document_id, user_role)


@mcp.tool()
def create_finance_ticket(scenario: str, summary: str, confirmed: bool = False, user_role: str = "employee") -> dict[str, Any]:
    """创建本地合成演示工单；必须 confirmed=true，绝不连接真实付款或记账系统。"""
    return service.create_ticket(scenario, summary, confirmed, user_role)


if __name__ == "__main__":
    mcp.run(transport="stdio")
