# -*- coding: utf-8 -*-
"""财务 AI 助手（Copilot）：预算图表分析、业务场景统计、对账差异报告、付款风险预审、差旅超标检查、连续追问。

设计约束：
- 离线规则解析／模板摘要：不调用外部 LLM，不读取或打印任何 Key；
- 所有 SQL 均为后端白名单内的参数化查询，用户输入只影响"选择哪个白名单查询和筛选参数"；
- 金额一律使用 Decimal 精确计算；
- 只读能力：不自动审批、支付、入账或建单；
- 分析上下文按 session_id 隔离，切换用户（新 session）不串用。
"""
from __future__ import annotations

import html
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .audit_trail import AgentAuditTrail
from .database import connect
from .scenarios import FinanceData, PaymentService, ReconciliationService
from .travel import TravelService

# 可进行跨部门聚合分析的角色（与现有权限体系一致）
ANALYSIS_ROLES = {
    "finance_reviewer", "finance_bp", "finance_manager", "treasury",
    "accountant", "auditor", "admin", "department_manager",
}
PAYMENT_FINANCE_ROLES = {
    "finance_reviewer", "finance_bp", "finance_manager", "treasury",
    "accountant", "auditor",
}

EXECUTION_MODE = "离线规则解析／模板摘要"
SYNTHETIC_DECLARATION = "全部为企业财务合成演示数据。"
CURRENT_FISCAL_YEAR = 2026

SCENARIO_NAMES = {"expense": "费用报销", "payment": "合同付款", "reconciliation": "银行对账"}
LEVEL_FALLBACK = "普通员工"


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    if whole == 0:
        return Decimal("0")
    return (part / whole * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money_text(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}"


def _cn_yuan(value: Decimal) -> str:
    """大额转万元展示，保留两位。"""
    wan = (value / Decimal("10000")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{wan:,}"


def _now_text() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# SVG 图表（本地生成，无外部依赖）
# ---------------------------------------------------------------------------

def _bar_chart_svg(title: str, rows: list[dict[str, Any]], *, value_key: str,
                   label_key: str, unit: str, threshold: Decimal | None = None,
                   threshold_label: str | None = None, value_fmt=None) -> str:
    """横向条形图：标题、数值标签、单位、阈值线；超阈值条形标红。"""
    if not rows:
        return ""
    esc = html.escape
    fmt = value_fmt or (lambda v: f"{v:,.2f}")
    width, left, right, row_h, top = 680, 150, 90, 36, 56
    height = top + row_h * len(rows) + 24
    values = [abs(_d(r[value_key])) for r in rows]
    max_v = max(values) if values else Decimal("1")
    if max_v == 0:
        max_v = Decimal("1")
    plot_w = width - left - right

    def x_of(v: Decimal) -> float:
        return float(v / max_v) * plot_w

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'font-family="Segoe UI, Microsoft YaHei, sans-serif" role="img" aria-label="{esc(title)}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{width / 2}" y="26" text-anchor="middle" font-size="15" font-weight="600" fill="#1f2937">{esc(title)}</text>',
        f'<text x="{width - 8}" y="26" text-anchor="end" font-size="11" fill="#6b7280">单位：{esc(unit)}</text>',
    ]
    for i, row in enumerate(rows):
        y = top + i * row_h
        value = abs(_d(row[value_key]))
        flagged = bool(row.get("flagged"))
        bar_w = max(x_of(value), 2)
        color = "#d64541" if flagged else "#2563eb"
        parts.append(f'<text x="{left - 10}" y="{y + row_h / 2 + 4}" text-anchor="end" font-size="12" fill="#374151">{esc(str(row[label_key]))}</text>')
        parts.append(f'<rect x="{left}" y="{y + 7}" width="{bar_w:.1f}" height="{row_h - 16}" rx="2" fill="{color}"><title>{esc(str(row[label_key]))}：{fmt(_d(row[value_key]))} {esc(unit)}</title></rect>')
        parts.append(f'<text x="{left + bar_w + 8:.1f}" y="{y + row_h / 2 + 4}" font-size="12" fill="{color}" font-weight="600">{fmt(_d(row[value_key]))}</text>')
    if threshold is not None and threshold > 0:
        tx = left + x_of(threshold)
        parts.append(f'<line x1="{tx:.1f}" y1="{top - 8}" x2="{tx:.1f}" y2="{height - 16}" stroke="#d97706" stroke-width="1.6" stroke-dasharray="6 4"/>')
        parts.append(f'<text x="{tx:.1f}" y="{top - 14}" text-anchor="middle" font-size="11" fill="#b45309">{esc(threshold_label or f"阈值 {threshold}")}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _html_table(headers: list[str], rows: list[list[Any]]) -> str:
    esc = html.escape
    head = "".join(f"<th>{esc(str(h))}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return (f'<table style="border-collapse:collapse;width:100%;font-size:12px">'
            f'<thead><tr style="background:#f3f4f6">{head}</tr></thead><tbody>{body}</tbody></table>')


# ---------------------------------------------------------------------------
# Copilot 主服务
# ---------------------------------------------------------------------------

class FinanceCopilot:
    """财务 AI 助手：新增能力全部只读，按 session 隔离分析上下文。"""

    def __init__(self, root: Path, db_path: Path | None = None) -> None:
        self.root = Path(root)
        self.db_path = db_path or (self.root / "data" / "finance_demo.db")
        self.data = FinanceData(self.root, self.db_path)
        self.payment_service = PaymentService(self.data)
        self.recon_service = ReconciliationService(self.data)
        self.audit = AgentAuditTrail(self.db_path)
        self._contexts: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # -- 入口 ---------------------------------------------------------------

    def try_handle(self, question: str, session_id: str) -> dict[str, Any] | None:
        """命中 Copilot 场景则返回结果，否则返回 None 交给原统一助手。"""
        from .enterprise_database import EnterpriseRepository
        repository = EnterpriseRepository(self.db_path)
        identity = repository.identity(session_id)
        text = question.strip()
        plan = self._parse(text, session_id)
        if plan is None:
            return None
        plan["_session"] = session_id
        request_id = "REQ-" + uuid.uuid4().hex[:16].upper()
        started = time.monotonic()
        trace: list[dict[str, Any]] = [{"step": "copilot_parse", "plan": plan["type"], "mode": EXECUTION_MODE}]
        runner = {
            "budget": self._run_budget,
            "budget_follow": self._run_budget_follow,
            "scenarios": self._run_scenarios,
            "recon_report": self._run_recon_report,
            "payment_risk": self._run_payment_risk,
            "travel_check": self._run_travel_check,
        }[plan["type"]]
        try:
            result = runner(plan, identity, text, trace)
        except PermissionError as exc:
            self.audit.record(request_id=request_id, session_id=session_id, identity=identity, question=text,
                              tool_name=plan["type"], arguments=plan, result=str(exc), status="permission_denied",
                              execution_mode=EXECUTION_MODE, started_at=started)
            raise
        except Exception as exc:
            self.audit.record(request_id=request_id, session_id=session_id, identity=identity, question=text,
                              tool_name=plan["type"], arguments=plan, result=str(exc), status="error",
                              execution_mode=EXECUTION_MODE, started_at=started)
            raise
        status = result.get("status", "completed")
        audit_id = self.audit.record(
            request_id=request_id, session_id=session_id, identity=identity, question=text,
            tool_name=plan["type"], arguments=plan, result=result.get("answer", ""), status=status,
            execution_mode=EXECUTION_MODE, started_at=started,
        )
        result["trace"].append({"step": "audit_persisted", "audit_id": audit_id})
        result["data"]["audit_request_id"] = request_id
        self._remember(session_id, result)
        return result

    # -- 离线规则解析 -------------------------------------------------------

    def _parse(self, text: str, session_id: str) -> dict[str, Any] | None:
        compact = re.sub(r"\s+", "", text)
        ctx = self._context(session_id)

        # 识别顺序：新场景优先；连续追问仅在“纯修饰语 + 已有上下文”时触发，
        # 且不含任何新场景关键词，避免“场景统计…按金额降序”被误判成追问。

        # 1) 差旅超标检查（自己的职级，任何登录用户可用；报销类旧问题走原助手）
        if re.search(r"出差", compact) and re.search(r"[0-9一二三四五六七八九十]{1,2}\s*天", compact) \
                and re.search(r"超标|检查|试算|上限", compact):
            return {"type": "travel_check"}

        # 2) 付款风险预审（区别于旧“请预检付款申请 PR-xxx”：必须带“报告”）
        if re.search(r"(预审|预检)", compact) and re.search(r"PR[-—]?\d{3}", compact) and "报告" in compact:
            rid = re.search(r"PR[-—]?(\d{3})", compact).group(0).upper().replace("—", "-")
            return {"type": "payment_risk", "request_id": rid}

        # 3) 对账差异分析报告（区别于旧“执行对账分析 xxx”：必须带“报告”）
        if "对账" in compact and "差异" in compact and "报告" in compact:
            return {"type": "recon_report"}

        # 4) 各业务场景事项统计
        if ("场景" in compact and ("事项" in compact or "金额" in compact)) or "事项数量和金额" in compact:
            return {"type": "scenarios"}

        # 5) 预算图表分析（无新阈值时从上下文继承，保持追问连贯）
        if re.search(r"预算(执行率|占用率)", compact) or ("各部门" in compact and "预算" in compact) \
                or ("预算" in compact and ("图表" in compact or "执行情况" in compact)):
            plan: dict[str, Any] = {"type": "budget"}
            if "占用" in compact:
                plan["metric"] = "occupancy"
            m = re.search(r"(\d{1,3})\s*%", compact) or re.search(r"超过(\d{1,3})", compact)
            if m:
                plan["threshold"] = Decimal(m.group(1))
            elif ctx and ctx.get("type") == "budget" and ctx.get("threshold") is not None:
                plan["threshold"] = ctx["threshold"]
            return plan

        # 6) 连续追问：只含修饰语，且已有分析上下文
        if ctx and re.search(r"(再看|换成|筛选|超过|降序|升序|排序|管理层报告|分析报告|生成报告)", compact) \
                and not re.search(r"场景|对账|预审|预检|出差|PR[-—]?\d", compact):
            plan = {"type": "budget_follow"}
            if "占用" in compact:
                plan["metric"] = "occupancy"
            elif "执行" in compact:
                plan["metric"] = "execution"
            m = re.search(r"(\d{1,3})\s*%", compact) or re.search(r"超过(\d{1,3})", compact)
            if m:
                plan["threshold"] = Decimal(m.group(1))
            if "降序" in compact:
                plan["sort"] = "desc"
            elif "升序" in compact:
                plan["sort"] = "asc"
            if "报告" in compact:
                plan["report"] = True
            if len(plan) > 1:
                return plan
        return None
        return None

    # -- 场景执行 -----------------------------------------------------------

    def _run_budget(self, plan: dict[str, Any], identity: dict[str, Any],
                    question: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
        self._require_analysis_role(identity, trace)
        metric = plan.get("metric", "execution")
        threshold = plan.get("threshold")
        rows = self._budget_rows(trace)
        return self._budget_result(rows, metric, threshold, identity, question, trace,
                                   follow_up=False)

    def _run_budget_follow(self, plan: dict[str, Any], identity: dict[str, Any],
                           question: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
        ctx = self._context(identity.get("session_id") or "") or {}
        # try_handle 已重新校验身份；上下文来自同一 session
        with self._lock:
            stored = self._contexts.get(plan.get("_session", ""), {})
        if not stored:
            return self._text_result(
                "budget_follow", question, identity,
                "当前会话还没有可追问的分析结果。请先提出一个分析类问题，例如“分析各部门预算执行情况并生成图表”。",
                trace, note="no_context")
        self._require_analysis_role(identity, trace)
        metric = plan.get("metric") or stored.get("metric", "execution")
        threshold = plan.get("threshold", stored.get("threshold"))
        sort = plan.get("sort")
        rows = stored["rows"]
        if sort == "desc":
            rows = sorted(rows, key=lambda r: _d(r["annual_budget"]), reverse=True)
        elif sort == "asc":
            rows = sorted(rows, key=lambda r: _d(r["annual_budget"]))
        result = self._budget_result(rows, metric, threshold, identity,
                                     stored.get("question", question), trace, follow_up=True)
        result["_context_rows"] = rows
        result["_context_metric"] = metric
        result["_context_threshold"] = threshold
        if plan.get("report"):
            result["data"]["report_html"] = self._build_budget_report(
                stored.get("question", question), identity, rows, metric, threshold,
                result["data"]["chart"]["svg"])
        return result

    def _run_scenarios(self, plan: dict[str, Any], identity: dict[str, Any],
                       question: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
        self._require_analysis_role(identity, trace)
        with connect(self.db_path) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT scenario, COUNT(*) AS item_count FROM work_items GROUP BY scenario"
            ).fetchall()]
            amounts = [dict(r) for r in conn.execute(
                "SELECT scenario, amount FROM work_items"
            ).fetchall()]
        trace.append({"step": "whitelist_query", "query": "work_items_group_by_scenario", "params": []})
        totals: dict[str, Decimal] = {}
        for row in amounts:
            totals[row["scenario"]] = totals.get(row["scenario"], Decimal("0")) + _d(row["amount"])
        stats = []
        for row in rows:
            name = SCENARIO_NAMES.get(row["scenario"], row["scenario"])
            stats.append({
                "label": name, "scenario": row["scenario"],
                "count": int(row["item_count"]),
                "amount": totals.get(row["scenario"], Decimal("0")),
                "flagged": False,
            })
        stats.sort(key=lambda r: r["amount"], reverse=True)
        for r in stats:
            r["amount_wan"] = _cn_yuan(r["amount"])
        total_amount = sum((r["amount"] for r in stats), Decimal("0"))
        total_count = sum(r["count"] for r in stats)
        answer = (
            f"各业务场景事项统计（按金额降序）：共 {total_count} 项、合计 {_money_text(total_amount)} 元。"
            + "；".join(f"{r['label']} {r['count']} 项 { _money_text(r['amount']) } 元" for r in stats)
        )
        svg = _bar_chart_svg(
            "各业务场景事项金额分布", stats, value_key="amount_wan", label_key="label",
            unit="万元",
            value_fmt=lambda v: f"{v:,.2f}",
        )
        detail_cols = ["业务场景", "事项数量", "金额合计（元）"]
        detail_rows = [[r["label"], r["count"], _money_text(r["amount"])] for r in stats]
        return self._result(
            "copilot_scenarios", "completed", answer, identity, question, trace,
            chart={"svg": svg, "title": "各业务场景事项金额分布", "unit": "万元"},
            detail={"columns": detail_cols, "rows": detail_rows},
            metric_note="金额按事项 amount 精确汇总（Decimal）；数量为 work_items 分组计数",
            summary=f"共 {total_count} 项事项、金额合计 {_money_text(total_amount)} 元；"
                    f"金额最高场景为 {stats[0]['label'] if stats else '—'}。",
        )

    def _run_recon_report(self, plan: dict[str, Any], identity: dict[str, Any],
                          question: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
        self._require_analysis_role(identity, trace)
        analysis = self.recon_service.analyze("manager")
        trace.append({"step": "reuse_service", "service": "ReconciliationService.analyze",
                      "read_only": True})
        with connect(self.db_path) as conn:
            runs = [dict(r) for r in conn.execute(
                "SELECT run_id, account_id, bank_record_count, ledger_record_count, "
                "matched_count, difference_count, created_at FROM reconciliation_runs "
                "ORDER BY created_at DESC LIMIT 8"
            ).fetchall()]
        trace.append({"step": "whitelist_query", "query": "recent_reconciliation_runs", "params": []})
        summary = {
            "account_label": analysis.get("account_id", "全部账户"),
            "bank_record_count": analysis.get("bank_record_count"),
            "ledger_record_count": analysis.get("ledger_record_count"),
            "matched_count": analysis.get("matched_count"),
            "difference_count": analysis.get("difference_count"),
            "status": analysis.get("status"),
            "summary_by_type": analysis.get("summary_by_type", {}),
        }
        answer_lines = [
            f"银行对账差异分析（只读，未生成新批次）：账户 {summary['account_label']}，"
            f"银行 {summary['bank_record_count']} 条、总账 {summary['ledger_record_count']} 条；"
            f"匹配 {summary['matched_count']} 笔，差异 {summary['difference_count']} 项（"
            f"涉及记录 {analysis.get('difference_record_count', '—')} 条）。",
        ]
        diffs = analysis.get("findings") or []
        type_names = {
            "duplicate_bank_record": "银行流水重复", "duplicate_ledger_record": "总账重复",
            "missing_in_ledger": "总账缺失", "missing_in_bank": "银行缺失",
            "amount_mismatch": "金额不一致", "currency_mismatch": "币种不一致",
            "date_mismatch": "日期不一致", "account_mismatch": "账户不一致",
        }
        for d in diffs[:5]:
            label = type_names.get(d.get("code"), d.get("code"))
            amount_note = f"（金额 {d['bank_amount']} 元）" if d.get("bank_amount") else ""
            answer_lines.append(f"· {label}：{d.get('record_id') or d.get('voucher_id')}{amount_note}")
        by_type = "、".join(f"{type_names.get(k, k)} {v} 项" for k, v in (summary["summary_by_type"] or {}).items())
        if by_type:
            answer_lines.append(f"差异构成：{by_type}。")
        run_rows = [[r["run_id"], r["account_id"], r["bank_record_count"], r["ledger_record_count"],
                     r["matched_count"], r["difference_count"], r["created_at"]] for r in runs]
        detail = {"columns": ["批次号", "账户", "银行流水", "总账", "匹配", "差异", "时间"],
                  "rows": run_rows}
        chart_svg = ""
        if runs:
            latest = runs[0]
            chart_rows = [
                {"label": "银行流水", "n": latest["bank_record_count"], "flagged": False},
                {"label": "总账记录", "n": latest["ledger_record_count"], "flagged": False},
                {"label": "匹配", "n": latest["matched_count"], "flagged": False},
                {"label": "差异", "n": latest["difference_count"], "flagged": latest["difference_count"] > 0},
            ]
            chart_svg = _bar_chart_svg(
                f"最近对账批次 {latest['run_id']}（{latest['account_id']}）",
                chart_rows, value_key="n", label_key="label", unit="笔",
                value_fmt=lambda v: f"{int(v)}",
            )
        return self._result(
            "copilot_recon", "completed", "\n".join(answer_lines), identity, question, trace,
            chart={"svg": chart_svg, "title": "最近对账批次概览", "unit": "笔"},
            detail=detail,
            metric_note="复用现有只读对账分析能力；仅查询历史批次，未创建新批次",
            summary=f"历史批次 {len(runs)} 个；最新批次差异 {runs[0]['difference_count'] if runs else '—'} 项。",
            extra={"analysis_summary": summary, "differences": diffs[:10]},
        )

    def _run_payment_risk(self, plan: dict[str, Any], identity: dict[str, Any],
                          question: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
        request_id = plan["request_id"]
        roles = set(identity.get("roles") or [])
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT requester, amount FROM payment_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        trace.append({"step": "whitelist_query", "query": "payment_request_owner", "params": [request_id]})
        if not row:
            return self._text_result("copilot_precheck", question, identity,
                                     f"未找到付款申请 {request_id}。", trace, status="not_found")
        if not (roles & PAYMENT_FINANCE_ROLES) and row["requester"] != identity["user_id"]:
            trace.append({"step": "role_check", "allowed": False})
            return self._text_result(
                "copilot_precheck", question, identity,
                f"没有权限预审 {request_id}：该申请不属于当前用户，且当前角色不在财务可见范围。",
                trace, status="forbidden")
        role = "manager" if (roles & PAYMENT_FINANCE_ROLES or roles & {"department_manager"}) else "employee"
        check = self.payment_service.check(request_id, role)
        trace.append({"step": "reuse_service", "service": "PaymentService.check", "read_only": True})
        findings = check.get("findings") or []
        amount = _d(row["amount"])
        codes = {f.get("code") for f in findings}
        if "missing_acceptance" in codes or amount > Decimal("50000"):
            risk = "高风险"
        elif findings:
            risk = "中风险"
        else:
            risk = "低风险"
        answer_lines = [
            f"付款申请 {request_id} 风险预审（只读）：金额 {_money_text(amount)} 元，结论 **{risk}**。",
        ]
        for f in findings:
            answer_lines.append(f"· {f.get('message')}")
        if not findings:
            answer_lines.append("· 预检未发现异常项。")
        detail_rows = [[f.get("code"), f.get("message") or "—", "待处理"] for f in findings] or [["—", "预检通过", "无"]]
        chart_svg = _bar_chart_svg(
            f"{request_id} 风险项分布",
            [{"label": "异常项", "n": len(findings), "flagged": bool(findings)}],
            value_key="n", label_key="label", unit="项", value_fmt=lambda v: f"{int(v)}",
        )
        return self._result(
            "copilot_precheck", "completed", "\n".join(answer_lines), identity, question, trace,
            chart={"svg": chart_svg, "title": f"{request_id} 风险项分布", "unit": "项"},
            detail={"columns": ["检查项", "说明", "状态"], "rows": detail_rows},
            metric_note="复用现有 PaymentService、附件登记与制度引用；未执行任何写入",
            summary=f"{request_id}：金额 {_money_text(amount)} 元，{risk}，异常 {len(findings)} 项。",
            extra={"risk_level": risk, "request_id": request_id},
        )

    def _run_travel_check(self, plan: dict[str, Any], identity: dict[str, Any],
                          question: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
        level = identity.get("level") or LEVEL_FALLBACK
        text = re.sub(r"\s+", "", question)
        # 中文数字归一：TravelService 只认阿拉伯数字天数
        for cn, num in {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
                        "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}.items():
            text = text.replace(f"{cn}天", f"{num}天")
        try:
            calc = TravelService(FinanceData(self.root, self.db_path)).precheck_from_text(
                f"{text}，{level}", "employee")
        except ValueError as exc:
            return self._text_result("copilot_travel", question, identity,
                                     f"未能解析出差信息：{exc}", trace, status="parse_error")
        trace.append({"step": "reuse_service", "service": "TravelService.precheck_from_text",
                      "read_only": True, "level": level})
        over = _d(calc.get("overlimit_amount"))
        limit = _d(calc.get("lodging_limit"))
        price = _d(calc.get("lodging_price"))
        nights = int(calc.get("nights") or 0)
        city = calc.get("city") or "—"
        total = _d(calc.get("total_amount"))
        if over > 0:
            advice = (f"住宿单价 { _money_text(price) } 元/晚超出 {level} 在 {city} 的住宿上限 "
                      f"{ _money_text(limit) } 元/晚，超标 { _money_text(over) } 元。"
                      "处理方式：1) 将住宿调整为限额内酒店；2) 如确因展会/旺季无法调整，"
                      "提交报销时需在说明中注明原因并附价格凭证，转财务负责人复核；"
                      "3) 超限额部分原则上个人承担。")
        else:
            advice = (f"住宿 { _money_text(price) } 元/晚未超出 {level} 在 {city} 的上限 "
                      f"{ _money_text(limit) } 元/晚，预检通过。预计总费用 { _money_text(total) } 元"
                      f"（住宿 {nights} 晚 + 伙食 + 市内交通）。")
        detail_rows = [
            ["住宿", _money_text(_d(calc.get('lodging_total'))), f"{_money_text(limit)}×{nights} 晚", "通过" if over == 0 else "超标"],
            ["伙食", _money_text(_d(calc.get('meal_total'))), "制度标准×天数", "通过"],
            ["市内交通", _money_text(_d(calc.get('transport_total'))), "制度标准", "通过"],
            ["合计", _money_text(total), "—", "超标" if over > 0 else "通过"],
        ]
        chart_rows = [
            {"label": "住宿", "n": _cn_yuan(_d(calc.get("lodging_total"))), "flagged": over > 0},
            {"label": "伙食", "n": _cn_yuan(_d(calc.get("meal_total"))), "flagged": False},
            {"label": "市内交通", "n": _cn_yuan(_d(calc.get("transport_total"))), "flagged": False},
        ]
        svg = _bar_chart_svg(f"{city}出差 {nights + 1} 天费用试算（{level}）", chart_rows,
                             value_key="n", label_key="label", unit="万元",
                             value_fmt=lambda v: f"{v:,.2f}")
        return self._result(
            "copilot_travel", "completed", advice, identity, question, trace,
            chart={"svg": svg, "title": "费用试算", "unit": "万元"},
            detail={"columns": ["费用项", "金额（元）", "口径", "结论"], "rows": detail_rows},
            metric_note="复用现有制度检索、职级与费用试算（TravelService）；仅检查系统内已有制度",
            summary=f"{city} {nights + 1} 天，住宿 { _money_text(price) } 元/晚，"
                    + (f"超标 { _money_text(over) } 元。" if over > 0 else "未超标。"),
        )

    # -- 预算分析共用逻辑 ----------------------------------------------------

    def _budget_rows(self, trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT department, project_id, annual_budget, committed, actual, frozen "
                "FROM budgets WHERE fiscal_year=? ORDER BY department, project_id",
                (CURRENT_FISCAL_YEAR,),
            ).fetchall()]
        trace.append({"step": "whitelist_query",
                      "query": "budgets_by_department_fiscal_year",
                      "params": [CURRENT_FISCAL_YEAR]})
        out = []
        for r in rows:
            annual, committed, actual, frozen = (_d(r["annual_budget"]), _d(r["committed"]),
                                                 _d(r["actual"]), _d(r["frozen"]))
            out.append({
                "department": r["department"], "project_id": r["project_id"],
                "annual_budget": annual, "committed": committed, "actual": actual,
                "frozen": frozen,
                "execution_rate": _pct(actual, annual),
                "occupancy_rate": _pct(actual + committed + frozen, annual),
            })
        return out

    def _budget_result(self, rows: list[dict[str, Any]], metric: str,
                       threshold: Decimal | None, identity: dict[str, Any],
                       question: str, trace: list[dict[str, Any]],
                       follow_up: bool) -> dict[str, Any]:
        metric_names = {"execution": "预算执行率", "occupancy": "预算占用率"}
        rate_key = "execution_rate" if metric == "execution" else "occupancy_rate"
        display = []
        for r in rows:
            rate = _d(r[rate_key])
            flagged = threshold is not None and rate > threshold
            display.append({
                "label": f"{r['department']}·{r['project_id']}",
                "rate": rate, "flagged": flagged,
                "annual_wan": _cn_yuan(r["annual_budget"]),
                "actual_wan": _cn_yuan(r["actual"]),
                "committed_wan": _cn_yuan(r["committed"]),
                "frozen_wan": _cn_yuan(r["frozen"]),
                **r,
            })
        display.sort(key=lambda r: r["rate"], reverse=True)
        thr_pct = threshold.quantize(Decimal("0.01")) if threshold is not None else None
        over = [d for d in display if d["flagged"]]
        svg = _bar_chart_svg(
            f"各部门{metric_names[metric]}（{CURRENT_FISCAL_YEAR} 财年）",
            [{"label": d["label"], "rate": d["rate"], "flagged": d["flagged"]} for d in display],
            value_key="rate", label_key="label", unit="%",
            threshold=thr_pct, threshold_label=f"阈值 {thr_pct}%" if thr_pct is not None else None,
            value_fmt=lambda v: f"{v:,.2f}%",
        )
        if thr_pct is not None:
            over_note = (f"超过阈值 {thr_pct}% 的有 {len(over)} 个：" + "、".join(d["label"] for d in over) + "。") if over else f"没有项目超过阈值 {thr_pct}%。"
        else:
            over_note = (f"{metric_names[metric]}最高为 {display[0]['label']}（{display[0]['rate']}%）。" if display else "")
        answer_lines = [
            f"{metric_names[metric]}＝" + (
                "actual／annual_budget" if metric == "execution"
                else "（actual＋committed＋frozen）／annual_budget") + "。",
            f"共 {len(display)} 个预算项目；{over_note}",
        ]
        detail_cols = ["部门·项目", "年度预算(万元)", "实际(万元)", "已承诺(万元)", "冻结(万元)",
                       "执行率%", "占用率%", "超标"]
        detail_rows = [[d["label"], d["annual_wan"], d["actual_wan"], d["committed_wan"],
                        d["frozen_wan"], str(d["execution_rate"]), str(d["occupancy_rate"]),
                        "是" if d["flagged"] else ""] for d in display]
        result = self._result(
            "copilot_budget", "completed", "\n".join(answer_lines), identity, question, trace,
            chart={"svg": svg, "title": f"各部门{metric_names[metric]}", "unit": "%"},
            detail={"columns": detail_cols, "rows": detail_rows},
            metric_note=("预算执行率＝actual／annual_budget；"
                         "预算占用率＝（actual＋committed＋frozen）／annual_budget；金额以 Decimal 精确计算"),
            summary=(f"{len(display)} 个预算项目，{metric_names[metric]}超标 "
                     f"{len(over) if thr_pct is not None else 0} 个。"),
        )
        result["_context_rows"] = rows
        result["_context_metric"] = metric
        result["_context_threshold"] = threshold
        return result

    def _build_budget_report(self, question: str, identity: dict[str, Any],
                             rows: list[dict[str, Any]], metric: str,
                             threshold: Decimal | None, chart_svg: str) -> str:
        metric_names = {"execution": "预算执行率", "occupancy": "预算占用率"}
        rate_key = "execution_rate" if metric == "execution" else "occupancy_rate"
        detail = [[r["department"] + "·" + r["project_id"],
                   _money_text(r["annual_budget"]), _money_text(r["actual"]),
                   _money_text(r["committed"]), _money_text(r["frozen"]),
                   str(r["execution_rate"]) + "%", str(r["occupancy_rate"]) + "%"] for r in rows]
        over = [r for r in rows if threshold is not None and _d(r[rate_key]) > threshold]
        summary = (f"{metric_names[metric]}分析：共 {len(rows)} 个预算项目。"
                     + (f"超过阈值 {threshold}% 的项目 {len(over)} 个，建议优先关注："
                        + "、".join(f"{r['department']}·{r['project_id']}（{_d(r[rate_key])}%）" for r in over[:5])
                        if over else "全部项目在阈值内。"))
        return self._report_html(
            title=f"各部门{metric_names[metric]}分析报告",
            question=question, identity=identity,
            metric_note=("预算执行率＝actual／annual_budget；"
                         "预算占用率＝（actual＋committed＋frozen）／annual_budget"),
            summary=summary, chart_svg=chart_svg,
            table_headers=["部门·项目", "年度预算(元)", "实际(元)", "已承诺(元)", "冻结(元)", "执行率", "占用率"],
            table_rows=detail,
            source_tables="budgets（fiscal_year=%d）" % CURRENT_FISCAL_YEAR,
        )

    # -- 报告与通用返回 ------------------------------------------------------

    def _report_html(self, *, title: str, question: str, identity: dict[str, Any],
                     metric_note: str, summary: str, chart_svg: str,
                     table_headers: list[str], table_rows: list[list[Any]],
                     source_tables: str) -> str:
        esc = html.escape
        return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>
body{{font-family:'Segoe UI','Microsoft YaHei',sans-serif;margin:32px;color:#1f2937;max-width:960px}}
h1{{font-size:20px}} h2{{font-size:15px;margin-top:24px;border-bottom:1px solid #e5e7eb;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;font-size:12px}} th,td{{border:1px solid #e5e7eb;padding:6px 8px;text-align:left}}
th{{background:#f3f4f6}} .meta{{color:#6b7280;font-size:12px}} .warn{{background:#fef3c7;padding:8px;border-radius:4px;font-size:12px}}
</style></head><body>
<h1>{esc(title)}</h1>
<p class="meta">原始问题：{esc(question)} ｜ 生成时间：{_now_text()} ｜ 当前用户：{esc(identity.get('display_name') or identity.get('user_id'))}（{esc(', '.join(identity.get('role_names') or []))}）</p>
<p class="meta">执行模式：{EXECUTION_MODE} ｜ {SYNTHETIC_DECLARATION}</p>
<h2>指标口径</h2><p>{esc(metric_note)}</p>
<h2>管理摘要</h2><p>{esc(summary)}</p>
<h2>图表</h2>{chart_svg or '<p>无图表</p>'}
<h2>数据明细</h2>{_html_table(table_headers, table_rows)}
<h2>来源表</h2><p>{esc(source_tables)}</p>
<p class="warn">声明：{SYNTHETIC_DECLARATION}本报告由离线规则解析与模板摘要生成，金额经 Decimal 精确计算。</p>
</body></html>"""

    def _result(self, scenario: str, status: str, answer: str,
                identity: dict[str, Any], question: str,
                trace: list[dict[str, Any]], *, chart: dict[str, Any] | None = None,
                detail: dict[str, Any] | None = None, metric_note: str = "",
                summary: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "copilot": True,
            "analysis_type": scenario,
            "execution_mode": EXECUTION_MODE,
            "metric_note": metric_note,
            "summary": summary,
            "question": question,
        }
        if chart:
            data["chart"] = chart
        if detail:
            data["detail"] = detail
        if extra:
            data.update(extra)
        # 分析类结果自动生成可下载的 HTML 报告（未显式提供时）
        if data.get("copilot") and data.get("chart", {}).get("svg") and not data.get("report_html"):
            detail = data.get("detail") or {"columns": [], "rows": []}
            source_map = {
                "copilot_budget": f"budgets（fiscal_year={CURRENT_FISCAL_YEAR}）",
                "copilot_scenarios": "work_items",
                "copilot_recon": "bank_transactions、ledger_entries、reconciliation_runs（只读）",
                "copilot_precheck": "payment_requests、contracts（PaymentService 只读预检）",
                "copilot_travel": "dim_city_tier、dim_expense_standard（TravelService 制度试算）",
            }
            data["report_html"] = self._report_html(
                title=f"{data['chart'].get('title', '分析')}·报告",
                question=question, identity=identity,
                metric_note=metric_note or "见数据明细口径列",
                summary=summary or answer[:200],
                chart_svg=data["chart"]["svg"],
                table_headers=detail.get("columns", []),
                table_rows=detail.get("rows", []),
                source_tables=source_map.get(scenario, "见执行轨迹"),
            )
        return {
            "scenario": scenario, "status": status, "answer": answer, "data": data,
            "citations": [], "trace": trace, "session_id": identity.get("session_id", ""),
            "human_confirmation_required": False, "pending_action": None,
            "platform_mode": "offline",
        }

    def _text_result(self, scenario: str, question: str, identity: dict[str, Any],
                     answer: str, trace: list[dict[str, Any]], *,
                     status: str = "completed", note: str = "") -> dict[str, Any]:
        result = self._result(scenario, status, answer, identity, question, trace,
                              metric_note="", summary=answer,
                              extra={"note": note} if note else None)
        return result

    # -- 权限与上下文 ---------------------------------------------------------

    def _require_analysis_role(self, identity: dict[str, Any],
                               trace: list[dict[str, Any]]) -> None:
        roles = set(identity.get("roles") or [])
        trace.append({"step": "role_check", "roles": sorted(roles),
                      "required_any": sorted(ANALYSIS_ROLES)})
        if not roles & ANALYSIS_ROLES:
            raise PermissionError(
                "当前角色无权进行跨部门聚合分析。该能力仅向财务、管理层、审计和系统管理员开放；"
                "普通员工请通过工作总览查看本人事项。")

    def _context(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            ctx = self._contexts.get(session_id)
            return dict(ctx) if ctx else None

    def _remember(self, session_id: str, result: dict[str, Any]) -> None:
        rows = result.pop("_context_rows", None)
        metric = result.pop("_context_metric", None)
        threshold = result.pop("_context_threshold", None)
        if rows is None:
            return
        with self._lock:
            self._contexts[session_id] = {
                "type": "budget", "rows": rows, "metric": metric,
                "threshold": threshold, "question": result["data"].get("question", ""),
            }
