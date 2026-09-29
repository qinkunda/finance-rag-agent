"""
agent_tools.py — Agent 工具层：查询企业标准库（写操作前的标准核查/预检/建单）
"""
import os
import sqlite3
from langchain_core.tools import tool

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "biz_rules.db")


def _query(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@tool
def get_travel_standard(city: str, level: str, expense_type: str = "住宿") -> str:
    """查询企业标准库中的差旅费用标准。
    当用户提出报销/出差需求、或问"标准是多少/上限多少"时必须调用。
    Args:
        city: 目的地城市，如 北京、深圳、成都
        level: 职级，如 普通员工、经理级
        expense_type: 费用类型，可选 住宿(默认)/伙食/市内交通
    """
    tier_row = _query("SELECT tier FROM dim_city_tier WHERE city=?", (city,))
    if not tier_row:
        return f"标准库中未找到城市[{city}]的分级，请与用户确认"
    tier = tier_row[0][0]
    if expense_type == "市内交通":
        row = _query("SELECT limit_amount FROM dim_expense_standard WHERE expense_type='市内交通'")
        return f"{city}市内交通实行包干制 {row[0][0]}元/人/天，不再单独报销票据"
    lv = "通用" if expense_type in ("伙食",) else level
    row = _query(
        "SELECT limit_amount FROM dim_expense_standard WHERE expense_type=? AND level=? AND tier=?",
        (expense_type, lv, tier if expense_type != "伙食" else tier),
    )
    if not row:
        return f"未找到{expense_type}标准（{level}/{tier}）"
    unit = "元/晚" if expense_type == "住宿" else "元/人/天"
    return f"{city}属于{tier}城市，{level}{expense_type}标准 {row[0][0]}{unit}"


@tool
def overlimit_check(city: str, level: str, actual_price: int) -> str:
    """住宿超标预检：对比实际单价与企业标准库住宿上限，返回超标判定与超支金额。
    当用户给出具体住宿单价时必须调用。
    Args:
        city: 目的地城市
        level: 职级
        actual_price: 实际住宿单价（元/晚）
    """
    tier_row = _query("SELECT tier FROM dim_city_tier WHERE city=?", (city,))
    if not tier_row:
        return f"标准库中未找到城市[{city}]的分级"
    tier = tier_row[0][0]
    row = _query(
        "SELECT limit_amount FROM dim_expense_standard WHERE expense_type='住宿' AND level=? AND tier=?",
        (level, tier),
    )
    limit = row[0][0]
    if actual_price > limit:
        return (f"超标：{actual_price}元/晚 > 标准{limit}元/晚，超{actual_price - limit}元/晚。"
                f"按制度第八条，超支部分原则上由个人承担（展会/政府接待等客观原因附证明经中心总监审批可据实）")
    return f"未超标：{actual_price}元/晚 ≤ 标准{limit}元/晚"


@tool
def create_expense_report(city: str, days: int, level: str, lodging_price: int = 0) -> str:
    """创建报销工单（演示环境为模拟创建，不写入真实系统）。
    【注意】本工具是写操作，调用前必须已向用户展示费用汇总并获得确认。
    Args:
        city: 目的地城市
        days: 出差天数
        level: 职级
        lodging_price: 住宿单价（元/晚），用于台账留档
    """
    role = "普通员工" if level == "普通员工" else "经理级"
    nodes = [r[0] for r in _query(
        "SELECT node_name FROM dim_approval_chain WHERE role=? ORDER BY node_order", (role,))]
    if days >= 5:
        nodes.append("财务BP")
    order_id = f"BX-AGENT-{city}-{days}D"
    return (f"工单 {order_id} 已创建（演示）。"
            f"住宿单价留档：{lodging_price}元/晚×{max(days - 1, 1)}晚；"
            f"审批链：{' → '.join(nodes)}"
            + ("（触发规则：跨市出差超过5天(含)，加签财务BP）" if days >= 5 else ""))