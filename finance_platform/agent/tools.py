# -*- coding: utf-8 -*-
"""企业工具层：把受权限保护的企业服务包装成 LLM 可调用的白名单工具。

这是复用到真实企业的关键层：每个工具内部做 RBAC 鉴权与只读约束，
LLM 拿到的是能力接口而不是数据库。换企业时只需新增/替换这里的工具实现。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from ..copilot import (ANALYSIS_ROLES, CURRENT_FISCAL_YEAR, PAYMENT_FINANCE_ROLES,
                      _bar_chart_svg, _d, _pct)
from ..database import connect
from ..scenarios import FinanceData, PaymentService, ReconciliationService
from ..policy_search import PolicySearch
from ..travel import TravelService


@dataclass
class ToolContext:
    root: Path
    db_path: Path
    identity: dict

    def require(self, roles: set[str], message: str) -> None:
        if not (set(self.identity.get("roles") or []) & roles):
            raise PermissionError(message)


@dataclass
class ToolResult:
    content: str
    viz: dict | None = None
    is_error: bool = False
    citations: list[dict] | None = None
    pending_action: dict | None = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, ToolContext], ToolResult]

    @property
    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


# ---------- 各工具实现（全部只读；写操作一律引导到工作流） ----------

def _analyze_budget(args: dict, ctx: ToolContext) -> ToolResult:
    ctx.require(ANALYSIS_ROLES, "跨部门预算聚合分析仅向财务、管理层、审计和系统管理员开放；普通员工请通过工作总览查看本人事项。")
    metric = args.get("metric") or "execution"
    metric = metric if metric in ("execution", "occupancy") else "execution"
    raw_thr = args.get("threshold")
    threshold = _d(raw_thr) if raw_thr not in (None, "") else None
    with connect(ctx.db_path) as conn:
        rows = conn.execute(
            "SELECT project_id, department, annual_budget, actual, committed, frozen "
            "FROM budgets WHERE fiscal_year=? ORDER BY department, project_id", (CURRENT_FISCAL_YEAR,)).fetchall()
    items = []
    for r in rows:
        annual, actual = _d(r["annual_budget"]), _d(r["actual"])
        if annual <= 0:
            continue
        committed, frozen = _d(r["committed"]), _d(r["frozen"])
        rate = _pct(actual, annual) if metric == "execution" else _pct(actual + committed + frozen, annual)
        items.append({"label": f"{r['department']}·{r['project_id']}", "rate": rate})
    items.sort(key=lambda x: x["rate"], reverse=True)
    over = [i for i in items if threshold is not None and i["rate"] > threshold]
    name = "预算执行率" if metric == "execution" else "预算占用率"
    formula = "actual／annual_budget" if metric == "execution" else "（actual＋committed＋frozen）／annual_budget"
    lines = [f"{name}＝{formula}；共 {len(items)} 个预算项目。"]
    for i in items:
        flag = "（超阈值）" if threshold is not None and i["rate"] > threshold else ""
        lines.append(f"· {i['label']}：{i['rate']}% {flag}")
    if threshold is not None:
        lines.append(f"超过阈值 {threshold}% 的项目共 {len(over)} 个。")
    chart = _bar_chart_svg(
        f"各部门{name}",
        [{"label": i["label"], "rate": i["rate"], "flagged": i in over} for i in items],
        value_key="rate", label_key="label", unit="%",
        threshold=threshold, threshold_label=(f"阈值 {threshold}%" if threshold is not None else None),
        value_fmt=lambda v: f"{v}%")
    viz = {"chart": {"title": f"各部门{name}", "svg": chart},
           "detail": {"columns": ["项目", name + "(%)"], "rows": [[i["label"], str(i["rate"])] for i in items]}}
    return ToolResult("\n".join(lines), viz=viz)


def _scenario_stats(args: dict, ctx: ToolContext) -> ToolResult:
    ctx.require(ANALYSIS_ROLES, "业务场景统计仅向财务、管理层、审计和系统管理员开放。")
    with connect(ctx.db_path) as conn:
        rows = conn.execute("SELECT scenario, amount FROM work_items").fetchall()
    agg: dict[str, dict] = {}
    for r in rows:
        bucket = agg.setdefault(r["scenario"], {"count": 0, "total": Decimal("0")})
        bucket["count"] += 1
        bucket["total"] += _d(r["amount"] or "0")
    items = sorted(agg.items(), key=lambda kv: kv[1]["total"], reverse=True)
    lines = [f"共 {sum(v['count'] for _, v in items)} 项，合计 {sum(v['total'] for _, v in items)} 元。按金额降序："]
    for name, v in items:
        lines.append(f"· {name}：{v['count']} 项，{v['total']} 元")
    chart = _bar_chart_svg(
        "各业务场景金额分布",
        [{"label": k, "total": v["total"]} for k, v in items],
        value_key="total", label_key="label", unit="元")
    viz = {"chart": {"title": "各业务场景金额分布", "svg": chart},
           "detail": {"columns": ["场景", "事项数", "金额(元)"],
                      "rows": [[k, str(v["count"]), str(v["total"])] for k, v in items]}}
    return ToolResult("\n".join(lines), viz=viz)


def _recon_report(args: dict, ctx: ToolContext) -> ToolResult:
    ctx.require(ANALYSIS_ROLES, "对账差异报告仅向财务、管理层、审计和系统管理员开放。")
    data = FinanceData(ctx.root, ctx.db_path)
    # 工具层已完成 ANALYSIS_ROLES 鉴权；服务内部要求 admin/manager 角色名，按既有复用约定传 "manager"
    analysis = ReconciliationService(data).analyze("manager", account_id=args.get("account_id") or None)
    sbt = analysis.get("summary_by_type") or {}
    type_names = {"duplicate_bank_record": "银行流水重复", "duplicate_ledger_record": "总账重复",
                  "missing_in_ledger": "总账缺失", "missing_in_bank": "银行缺失",
                  "amount_mismatch": "金额不一致", "currency_mismatch": "币种不一致", "date_mismatch": "日期不一致"}
    findings = analysis.get("findings") or []
    lines = [f"账户 {analysis.get('account_id', 'all')}：银行 {analysis.get('bank_record_count')} 条、"
             f"总账 {analysis.get('ledger_record_count')} 条；匹配 {analysis.get('matched_count')} 笔，"
             f"差异 {analysis.get('difference_count')} 项（记录 {analysis.get('difference_record_count')} 条）。",
             "差异构成：" + ("、".join(f"{type_names.get(k, k)} {v} 项" for k, v in sbt.items()) or "无")]
    for f_ in findings[:8]:
        lines.append(f"· {type_names.get(f_.get('code'), f_.get('code'))}：{f_.get('record_id') or f_.get('voucher_id')}")
    chart = _bar_chart_svg(
        "对账匹配情况",
        [{"label": n, "count": c} for n, c in [
            ("银行流水", analysis.get("bank_record_count", 0)),
            ("总账分录", analysis.get("ledger_record_count", 0)),
            ("匹配", analysis.get("matched_count", 0)),
            ("差异", analysis.get("difference_count", 0))]],
        value_key="count", label_key="label", unit="笔", value_fmt=lambda v: f"{int(v)}")
    viz = {"chart": {"title": "对账匹配情况", "svg": chart},
           "detail": {"columns": ["差异类型", "记录"], "rows": [[type_names.get(f_.get('code'), f_.get('code')),
                                                                f_.get('record_id') or f_.get('voucher_id') or ""] for f_ in findings]}}
    return ToolResult("\n".join(lines), viz=viz)


def _payment_risk(args: dict, ctx: ToolContext) -> ToolResult:
    rid = str(args.get("request_id") or "").strip().upper()
    if not rid:
        return ToolResult("缺少必需参数 request_id", is_error=True)
    ident = ctx.identity
    with connect(ctx.db_path) as conn:
        row = conn.execute("SELECT requester, amount FROM payment_requests WHERE request_id=?", (rid,)).fetchone()
    if row is None:
        return ToolResult(f"未找到付款申请 {rid}。", is_error=True)
    if not (set(ident.get("roles") or []) & PAYMENT_FINANCE_ROLES) and row["requester"] != ident["user_id"]:
        raise PermissionError(f"没有权限预审 {rid}：该申请不属于当前用户，且当前角色不在财务可见范围。")
    roles = set(ident.get("roles") or [])
    service_role = "manager" if (roles & PAYMENT_FINANCE_ROLES or roles & {"department_manager"}) else "employee"
    check = PaymentService(FinanceData(ctx.root, ctx.db_path)).check(rid, service_role)
    findings = check.get("findings") or []
    amount = _d(row["amount"])
    codes = {f.get("code") for f in findings}
    if "missing_acceptance" in codes or amount > Decimal("50000"):
        risk = "高风险"
    elif findings:
        risk = "中风险"
    else:
        risk = "低风险"
    lines = [f"付款申请 {rid}：金额 {amount} 元；风险预审结论：{risk}。"]
    for f_ in findings:
        lines.append(f"· {f_.get('message')}")
    if not findings:
        lines.append("· 预检未发现异常项。")
    viz = {"chart": {"title": f"{rid} 风险项", "svg": _bar_chart_svg(
                f"{rid} 风险项",
                [{"label": "异常项", "n": len(findings), "flagged": bool(findings)}],
                value_key="n", label_key="label", unit="项", value_fmt=lambda v: f"{int(v)}")},
           "detail": {"columns": ["代码", "说明", "状态"],
                      "rows": [[f_.get("code"), f_.get("message") or "—", "待处理"] for f_ in findings] or [["—", "预检通过", "无"]]}}
    return ToolResult("\n".join(lines), viz=viz)


def _travel_precheck(args: dict, ctx: ToolContext) -> ToolResult:
    city = str(args.get("city") or "").strip()
    days, lodging = args.get("days"), args.get("lodging_price")
    missing = [n for n, v in (("city", city), ("days", days), ("lodging_price", lodging)) if v in (None, "")]
    if missing:
        return ToolResult(f"缺少必需参数：{', '.join(missing)}", is_error=True)
    level = ctx.identity.get("level") or "员工"
    text = f"去{city}出差{int(days)}天，住宿每晚{lodging}元，{level}"
    calc = TravelService(FinanceData(ctx.root, ctx.db_path)).precheck_from_text(text, "employee")
    rows = [["出发城市", calc.get("city") or city], ["出差天数", str(calc.get("days"))],
            ["住宿晚数", str(calc.get("nights"))], ["住宿单价(元/晚)", str(_d(lodging))],
            ["住宿限额(元/晚)", str(_d(calc.get("lodging_limit") or "0"))],
            ["住宿合计(元)", str(_d(calc.get("lodging_total") or "0"))],
            ["伙食合计(元)", str(_d(calc.get("meal_total") or "0"))],
            ["市内交通(元)", str(_d(calc.get("transport_total") or "0"))],
            ["试算总额(元)", str(_d(calc.get("total_amount") or "0"))],
            ["超标金额(元)", str(_d(calc.get("overlimit_amount") or "0"))],
            ["判定", calc.get("status") or "—"]]
    advice = [finding["message"] for finding in calc.get("findings") or []]
    lines = [f"制度试算（{level}，{calc.get('city') or city}）：住宿限额 {calc.get('lodging_limit')} 元/晚，"
             f"试算 {int(days)} 天合计 {calc.get('total_amount')} 元，超标 {calc.get('overlimit_amount')} 元，"
             f"判定：{calc.get('status')}。"]
    if advice:
        lines.append("处理建议：" + "；".join(advice))
    viz = {"detail": {"columns": ["项目", "值"], "rows": rows}}
    return ToolResult("\n".join(lines), viz=viz)


def _search_policy(args: dict, ctx: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult("缺少必需参数 query", is_error=True)
    role = ctx.identity.get("knowledge_role") or "employee"
    answer = PolicySearch(ctx.root, ctx.db_path).answer(query, user_role=role)
    if answer.get("blocked"):
        return ToolResult(answer["answer"], is_error=True)
    citations = "；".join(
        f"{c.get('document_id', '')}《{c.get('title', '')}》{c.get('version', '')}"
        for c in answer.get("citations") or []) or "无"
    return ToolResult(f"制度检索结果：\n{answer['answer']}\n引用条款：{citations}",
                      citations=answer.get("citations") or [])


def _prepare_expense(args: dict, ctx: ToolContext) -> ToolResult:
    result = _travel_precheck(args, ctx)
    if result.is_error:
        return result
    calc = TravelService(FinanceData(ctx.root, ctx.db_path)).precheck_from_text(
        f"去{str(args.get('city') or '').strip()}出差{int(args['days'])}天，住宿每晚{args['lodging_price']}元，"
        f"{ctx.identity.get('level') or '员工'}", "employee")
    result.pending_action = {
        "type": "expense_draft", "view": "business", "title": "差旅报销预填草稿",
        "payload": {"city": calc["city"], "days": int(args["days"]), "lodging_price": str(args["lodging_price"])},
        "preview": calc, "requires_confirmation": True,
    }
    result.content += "\n已生成只读预填草稿，必须由用户在业务办理页面核对并确认后才能创建。"
    return result


def _prepare_payment(args: dict, ctx: ToolContext) -> ToolResult:
    result = _payment_risk(args, ctx)
    if result.is_error:
        return result
    request_id = str(args.get("request_id") or "").strip().upper()
    with connect(ctx.db_path) as conn:
        existing = conn.execute(
            "SELECT 1 FROM work_items WHERE scenario='payment' AND record_id=?", (request_id,)
        ).fetchone()
    if existing:
        return ToolResult(f"{request_id} 已进入流程，不能重复创建预填草稿。", is_error=True)
    result.pending_action = {
        "type": "payment_draft", "view": "business", "title": "合同付款预填草稿",
        "payload": {"request_id": request_id}, "requires_confirmation": True,
    }
    result.content += "\n已生成只读预填草稿，必须由用户在业务办理页面核对并确认后才能进入流程。"
    return result


# ---------- 工具注册表 ----------

def build_tools() -> dict[str, Tool]:
    tools = [
        Tool("analyze_budget",
             "跨部门预算执行率/占用率分析（只读）。metric=execution(实际/年度) 或 occupancy(含在途冻结)；threshold 为百分比阈值如 85。",
             {"type": "object", "properties": {
                 "metric": {"type": "string", "enum": ["execution", "occupancy"]},
                 "threshold": {"type": "number"}}, "required": []},
             _analyze_budget),
        Tool("scenario_stats",
             "统计各业务场景（合同付款/费用报销/银行对账等）的事项数量与金额，按金额降序（只读）。",
             {"type": "object", "properties": {}}, _scenario_stats),
        Tool("recon_report",
             "银行对账差异分析报告（只读，不生成新对账批次）。account_id 默认 all。",
             {"type": "object", "properties": {"account_id": {"type": "string"}}}, _recon_report),
        Tool("payment_risk",
             "付款申请风险预审（只读，不执行支付）。request_id 如 PR-002。仅财务角色或申请人本人可查。",
             {"type": "object", "properties": {"request_id": {"type": "string"}}, "required": ["request_id"]},
             _payment_risk),
        Tool("travel_precheck",
             "差旅住宿制度试算（只读）：按当前用户职级判断住宿单价是否超标并给出处理建议。",
             {"type": "object", "properties": {
                 "city": {"type": "string"}, "days": {"type": "integer", "minimum": 1},
                 "lodging_price": {"type": "number", "minimum": 0}},
              "required": ["city", "days", "lodging_price"]}, _travel_precheck),
        Tool("prepare_expense",
             "根据用户明确表达的新建报销意图，试算并生成差旅报销预填草稿；只读，不得直接创建记录，必须由用户确认。",
             {"type": "object", "properties": {
                 "city": {"type": "string"}, "days": {"type": "integer", "minimum": 1},
                 "lodging_price": {"type": "number", "minimum": 0}},
              "required": ["city", "days", "lodging_price"]}, _prepare_expense),
        Tool("prepare_payment",
             "根据用户明确表达的新建付款流程意图，生成合同付款预填草稿；只读，不得直接提交，必须由用户确认。",
             {"type": "object", "properties": {"request_id": {"type": "string"}}, "required": ["request_id"]},
             _prepare_payment),
        Tool("search_policy",
             "检索企业财务制度条款（住宿费上限、报销标准等），返回条款原文与引用。涉及制度依据的问题必须先调用。",
             {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
             _search_policy),
    ]
    return {t.name: t for t in tools}
