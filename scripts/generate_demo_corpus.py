"""生成可复现的统一财务平台合成语料；不读取任何本机业务文件或密钥。"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"
SYNTHETIC = "本文件为星汉集团合成示例，仅用于软件测试，不对应任何真实企业、人员或交易。"

# 文件名：标题、场景、版本、状态、生效日期、条款。
POLICIES = {
    "payment_policy_v0_9.pdf": (
        "付款管理办法（旧版）", "payment", "0.9", "archived", "2024-01-01",
        [
            "第一条 旧版付款申请应关联合同和供应商信息。",
            "第二条 旧版预付款比例上限为百分之四十，本条款已失效，不得用于当前业务判断。",
            "第三条 旧版审批要求已由新版本替代。",
            "第四条 历史业务查询必须明确选择旧版，不得与现行制度混用。",
            "第五条 本办法自2025年12月31日起失效。",
        ],
    ),
    "payment_policy_v1_0.pdf": (
        "付款管理办法", "payment", "1.0", "active", "2026-01-01",
        [
            "第一条 付款申请必须关联已审批合同、供应商主数据和预算项目，缺一不可。",
            "第二条 预付款比例不得超过合同含税总额的百分之三十；超过时须提交财务负责人专项审批。",
            "第三条 到货款支付前必须取得验收单和合法有效的发票；未取得验收单时系统只能转人工复核。",
            "第四条 质保金比例为合同含税总额的百分之十，质保期满且无未决质量问题后方可申请。",
            "第五条 收款账户必须与供应商主数据一致；账户变更须由财务经理线下复核，本系统不自动修改。",
            "第六条 单笔付款超过五万元时，审批链在部门负责人之后增加财务负责人节点。",
            "第七条 发票状态无效或发票金额与申请金额不一致时，必须转人工复核。",
            "第八条 本系统只执行预检和生成模拟工单，不自动付款、不自动记账。",
            "第九条 本办法版本为V1.0，自2026年1月1日起生效。",
        ],
    ),
    "contract_review_guide_v1_0.pdf": (
        "合同财务条款审核指引", "payment", "1.0", "active", "2026-01-01",
        [
            "第一条 审核范围包括合同金额、税率、付款里程碑、验收条件、发票要求、质保金和收款账户。",
            "第二条 付款里程碑必须使用可验证的事件描述，不得只写适时付款或按甲方要求付款。",
            "第三条 合同条款与结构化字段不一致时，以已签署合同原文为证据并转人工复核，不得静默覆盖。",
            "第四条 预付款、到货款和质保金比例合计不得超过合同总额的百分之一百。",
            "第五条 对缺少税率或发票类型的合同，审核结论必须为待补充，不得推测税务处理。",
            "第六条 合同状态为草稿、已失效或未审批时，不得进入付款审批。",
            "第七条 本指引版本为V1.0，自2026年1月1日起生效。",
        ],
    ),
    "supplier_master_policy_v1_0.pdf": (
        "供应商主数据与账户管理规范", "payment", "1.0", "active", "2026-02-01",
        [
            "第一条 供应商必须具有唯一主数据编号，状态包括启用、停用和冻结。",
            "第二条 付款账户指纹必须与供应商主数据一致，系统不保存真实银行账号。",
            "第三条 停用供应商不得发起新的付款申请，历史单据仍需保留审计链路。",
            "第四条 高风险供应商的付款申请需要增加合规复核节点。",
            "第五条 账户变更不得由对话助手直接修改，必须进入独立复核流程。",
            "第六条 本规范版本为V1.0，自2026年2月1日起生效。",
        ],
    ),
    "budget_policy_v0_9.pdf": (
        "预算管理办法（旧版）", "budget", "0.9", "archived", "2024-01-01",
        [
            "第一条 旧版可用预算未包含冻结金额，本口径已经失效。",
            "第二条 旧版预算调整流程由线下表单完成。",
            "第三条 历史分析可以使用旧口径，但必须显式标注版本。",
            "第四条 本办法自2025年12月31日起失效。",
        ],
    ),
    "budget_policy_v1_0.pdf": (
        "预算管理与调整办法", "budget", "1.0", "active", "2026-02-01",
        [
            "第一条 可用预算等于年度预算减去已承诺金额、已发生金额和冻结金额，计算由规则程序完成。",
            "第二条 申请金额不超过可用预算时，可进入部门负责人和财务BP审批。",
            "第三条 申请金额超过可用预算时，不得直接提交付款，应先发起预算调整或削减申请金额。",
            "第四条 预算调整必须写明来源项目、目标项目、调整金额和业务原因，并保持总预算平衡。",
            "第五条 普通员工可以查看公开预算制度；部门预算明细仅向经理和管理员角色开放。",
            "第六条 冻结金额用于已审批但尚未承诺或发生的预留事项，不得重复占用。",
            "第七条 本办法版本为V1.0，自2026年2月1日起生效。",
        ],
    ),
    "reconciliation_policy_v1_0.pdf": (
        "财务对账与差异处理规范", "reconciliation", "1.0", "active", "2026-03-01",
        [
            "第一条 对账以银行交易流水号和总账外部参考号进行精确匹配，不得仅凭金额相同自动核销。",
            "第二条 已匹配但金额不一致的记录标记为金额差异，必须由会计人员复核。",
            "第三条 银行有记录而总账无记录的，标记为总账缺失；总账有记录而银行无记录的，标记为银行未达。",
            "第四条 同一银行流水号或同一总账外部参考号出现多次时标记为重复记录，不得自动汇总后掩盖差异。",
            "第五条 金额一致但币种或交易日期不一致时，仍应分别标记为币种差异或日期差异。",
            "第六条 差异处理结果必须保留原始记录标识、差异类型和处理状态，支持审计追溯。",
            "第七条 本规范版本为V1.0，自2026年3月1日起生效。",
        ],
    ),
    "invoice_policy_v1_0.pdf": (
        "发票与税务资料管理细则", "invoice", "1.0", "active", "2026-01-15",
        [
            "第一条 发票号码、开票日期、销售方、购买方、金额和税额应按结构化字段保存。",
            "第二条 发票影像与结构化字段不一致时，状态设为待复核，不得直接用于自动付款。",
            "第三条 税率和税收政策的判断必须记录政策来源及生效日期；知识库没有依据时应拒绝推测。",
            "第四条 发票金额与本次申请金额不一致时，应核对是否属于合法分次开票；演示系统一律转人工。",
            "第五条 本细则不提供更广泛税务意见，具体税务处理由具备资质的人员复核。",
            "第六条 本细则版本为V1.0，自2026年1月15日起生效。",
        ],
    ),
    "shared_service_workflow_v1_0.pdf": (
        "财务共享工单流转规则", "workflow", "1.0", "active", "2026-03-01",
        [
            "第一条 工单类型包括费用报销、付款预检、预算调整、对账差异和制度咨询。",
            "第二条 知识查询和只读预检可以直接返回；创建工单必须获得用户明确确认。",
            "第三条 缺少合同、验收单、发票或预算依据时，工单进入待补充状态并列出缺口。",
            "第四条 权限不足、规则冲突和模型输出校验失败时，必须转人工，不得自动放行。",
            "第五条 审计日志记录用户角色、场景、工具、结果和规则版本，不记录完整敏感单据。",
            "第六条 所有场景共用统一会话入口，用户无需分别打开报销、付款、预算或对账系统。",
            "第七条 本规则版本为V1.0，自2026年3月1日起生效。",
        ],
    ),
    "payment_exception_workflow_v1_0.pdf": (
        "付款异常复核与工单指引", "workflow", "1.0", "active", "2026-03-10",
        [
            "第一条 付款预检发现异常时，只允许创建待复核工单，不得返回已付款或已审批。",
            "第二条 工单摘要应包含申请编号、异常类型、合同编号和规则版本。",
            "第三条 用户确认仅表示同意创建合成演示工单，不代表同意真实资金划转。",
            "第四条 取消确认后不得写入工单，原待办动作应从当前会话清除。",
            "第五条 本指引版本为V1.0，自2026年3月10日起生效。",
        ],
    ),
}


def write_pdf(path: Path, title: str, clauses: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    width, height = A4
    y = height - 54
    c.setFont("STSong-Light", 16)
    c.drawString(54, y, f"星汉集团（合成示例）{title}")
    y -= 30
    c.setFont("STSong-Light", 9)
    c.drawString(54, y, SYNTHETIC)
    y -= 24
    c.setFont("STSong-Light", 11)
    for clause in clauses:
        if y < 64:
            c.showPage()
            c.setFont("STSong-Light", 11)
            y = height - 54
        c.drawString(54, y, clause)
        y -= 25
    c.save()
    # ReportLab 的默认文件头注释含 4 个非 UTF-8 字节。替换为等长 ASCII
    # 注释不会改变任何 xref 偏移，PDF 仍可正常解析，且能无损走文本传输。
    payload = path.read_bytes().replace(b"%\x93\x8c\x8b\x9e", b"%DEMO", 1)
    payload.decode("ascii")
    path.write_bytes(payload)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def milestones(advance: str = "0.30", delivery: str = "0.60", warranty: str = "0.10") -> list[dict]:
    return [
        {"code": "advance", "name": "预付款", "ratio": advance, "requires": ["contract_approved", "invoice"]},
        {"code": "delivery", "name": "到货款", "ratio": delivery, "requires": ["contract_approved", "invoice", "acceptance"]},
        {"code": "warranty", "name": "质保金", "ratio": warranty, "requires": ["contract_approved", "warranty_expired"]},
    ]


def contract(contract_id: str, vendor_id: str, project_id: str, title: str, amount: str,
             status: str = "active", approved: bool = True, ratios: tuple[str, str, str] = ("0.30", "0.60", "0.10")) -> dict:
    return {
        "contract_id": contract_id, "vendor_id": vendor_id, "project_id": project_id,
        "title": title, "total_amount": amount, "currency": "CNY", "approved": approved,
        "status": status, "signed_at": "2026-01-15", "milestones": milestones(*ratios),
    }


def payment(request_id: str, contract_id: str, milestone: str, amount: str, account: str,
            invoice_id: str | None = None, acceptance_id: str | None = None,
            warranty_expired: bool = False) -> dict:
    return {
        "request_id": request_id, "contract_id": contract_id, "milestone": milestone,
        "amount": amount, "invoice_id": invoice_id, "acceptance_id": acceptance_id,
        "warranty_expired": warranty_expired, "account_fingerprint": account,
        "requester": "synthetic-requester", "created_at": "2026-03-12T09:00:00+08:00",
    }


def invoice(invoice_id: str, request_id: str, amount: str, status: str = "valid") -> dict:
    return {
        "invoice_id": invoice_id, "request_id": request_id,
        "invoice_number": f"SYN-{invoice_id}", "status": status, "amount": amount,
        "tax_rate": "0.06", "issue_date": "2026-03-10",
    }


def acceptance(acceptance_id: str, contract_id: str, milestone: str = "delivery", status: str = "approved") -> dict:
    return {
        "acceptance_id": acceptance_id, "contract_id": contract_id, "milestone": milestone,
        "status": status, "accepted_at": "2026-03-11" if status == "approved" else None,
    }


def main() -> None:
    for name, (title, _, _, _, _, clauses) in POLICIES.items():
        write_pdf(DATA / "raw" / "policies" / name, title, clauses)

    write_json(DATA / "raw" / "vendors" / "vendors.json", [
        {"vendor_id": f"V-{i:03d}", "vendor_name": f"合成供应商{i:02d}",
         "account_fingerprint": f"ACCT-DEMO-{i:03d}", "status": status, "risk_level": risk}
        for i, status, risk in [
            (1, "active", "low"), (2, "active", "low"), (3, "active", "medium"),
            (4, "active", "medium"), (5, "active", "low"), (6, "active", "high"),
            (7, "suspended", "high"), (8, "active", "low"),
        ]
    ])

    write_csv(DATA / "raw" / "budget" / "budgets.csv", [
        {"department": "供应链部", "project_id": "LOG-2026", "cost_center": "CC-SC-01", "fiscal_year": 2026, "annual_budget": "200000.00", "committed": "80000.00", "actual": "50000.00", "frozen": "0.00"},
        {"department": "信息部", "project_id": "IT-2026", "cost_center": "CC-IT-01", "fiscal_year": 2026, "annual_budget": "500000.00", "committed": "100000.00", "actual": "80000.00", "frozen": "20000.00"},
        {"department": "人力资源部", "project_id": "HR-2026", "cost_center": "CC-HR-01", "fiscal_year": 2026, "annual_budget": "80000.00", "committed": "10000.00", "actual": "5000.00", "frozen": "0.00"},
        {"department": "制造部", "project_id": "MFG-2026", "cost_center": "CC-MFG-01", "fiscal_year": 2026, "annual_budget": "300000.00", "committed": "100000.00", "actual": "50000.00", "frozen": "20000.00"},
        {"department": "市场部", "project_id": "MKT-2026", "cost_center": "CC-MKT-01", "fiscal_year": 2026, "annual_budget": "120000.00", "committed": "20000.00", "actual": "10000.00", "frozen": "5000.00"},
        {"department": "研发部", "project_id": "RND-2026", "cost_center": "CC-RND-01", "fiscal_year": 2026, "annual_budget": "400000.00", "committed": "150000.00", "actual": "50000.00", "frozen": "20000.00"},
        {"department": "财务部", "project_id": "FIN-2026", "cost_center": "CC-FIN-01", "fiscal_year": 2026, "annual_budget": "150000.00", "committed": "20000.00", "actual": "10000.00", "frozen": "0.00"},
        {"department": "采购部", "project_id": "PUR-2026", "cost_center": "CC-PUR-01", "fiscal_year": 2026, "annual_budget": "260000.00", "committed": "60000.00", "actual": "40000.00", "frozen": "10000.00"},
    ])

    contracts = [
        contract("HT-2026-001", "V-001", "LOG-2026", "合成运输设备采购合同", "100000.00"),
        contract("HT-2026-002", "V-002", "IT-2026", "合成数据平台服务合同", "240000.00", ratios=("0.20", "0.70", "0.10")),
        contract("HT-2026-003", "V-003", "HR-2026", "合成培训服务草稿合同", "80000.00", status="draft", approved=False),
        contract("HT-2026-004", "V-004", "MFG-2026", "合成检测设备采购合同", "150000.00"),
        contract("HT-2026-005", "V-005", "LOG-2026", "合成仓储服务失效合同", "200000.00", status="expired"),
        contract("HT-2026-006", "V-006", "IT-2026", "合成模型平台建设合同", "320000.00"),
        contract("HT-2026-007", "V-007", "MKT-2026", "合成市场服务合同", "90000.00"),
        contract("HT-2026-008", "V-008", "RND-2026", "合成研发设备采购合同", "120000.00"),
    ]
    write_json(DATA / "raw" / "contracts" / "contracts.json", contracts)

    payments = [
        payment("PR-001", "HT-2026-001", "advance", "30000.00", "ACCT-DEMO-001", "INV-001"),
        payment("PR-002", "HT-2026-001", "delivery", "60000.00", "ACCT-DEMO-001", "INV-002"),
        payment("PR-003", "HT-2026-001", "advance", "70000.00", "ACCT-DEMO-001", "INV-003"),
        payment("PR-004", "HT-2026-002", "advance", "48000.00", "ACCT-DEMO-002", "INV-004"),
        payment("PR-005", "HT-2026-002", "delivery", "168000.00", "ACCT-DEMO-002", "INV-005", "ACC-005"),
        payment("PR-006", "HT-2026-003", "advance", "24000.00", "ACCT-DEMO-003", "INV-006"),
        payment("PR-007", "HT-2026-004", "delivery", "90000.00", "ACCT-DEMO-999", "INV-007", "ACC-007"),
        payment("PR-008", "HT-2026-004", "warranty", "15000.00", "ACCT-DEMO-004"),
        payment("PR-009", "HT-2026-005", "advance", "45000.00", "ACCT-DEMO-005", "INV-009"),
        payment("PR-010", "HT-2026-006", "advance", "96000.00", "ACCT-DEMO-006"),
        payment("PR-011", "HT-2026-007", "advance", "27000.00", "ACCT-DEMO-007", "INV-011"),
        payment("PR-012", "HT-2026-008", "advance", "36000.00", "ACCT-DEMO-008", "INV-012"),
        payment("PR-013", "HT-2026-008", "delivery", "72000.00", "ACCT-DEMO-008", "INV-013", "ACC-013"),
        payment("PR-014", "HT-2026-008", "warranty", "12000.00", "ACCT-DEMO-008", warranty_expired=True),
        payment("PR-015", "HT-2026-006", "advance", "96000.00", "ACCT-DEMO-006", "INV-015"),
        payment("PR-016", "HT-2026-006", "delivery", "192000.00", "ACCT-DEMO-006", "INV-016", "ACC-016"),
    ]
    write_json(DATA / "raw" / "transactions" / "payment_requests.json", payments)
    invoice_amounts = {
        "PR-001": "30000.00", "PR-002": "60000.00", "PR-003": "70000.00",
        "PR-004": "48000.00", "PR-005": "168000.00", "PR-006": "24000.00",
        "PR-007": "90000.00", "PR-009": "45000.00", "PR-011": "27000.00",
        "PR-012": "36000.00", "PR-013": "72000.00", "PR-015": "96000.00", "PR-016": "192000.00",
    }
    invoices = [invoice(f"INV-{request_id.split('-')[1]}", request_id, amount)
                for request_id, amount in invoice_amounts.items()]
    invoices.append(invoice("INV-017", "PR-013", "70000.00", "amount_mismatch"))
    # 用 INV-017 替换 PR-013 的默认发票，形成“申请金额一致但发票金额异常”的独立案例。
    invoices = [x for x in invoices if not (x["request_id"] == "PR-013" and x["invoice_id"] == "INV-013")]
    for row in payments:
        if row["request_id"] == "PR-013":
            row["invoice_id"] = "INV-017"
    write_json(DATA / "raw" / "transactions" / "invoices.json", invoices)
    write_json(DATA / "raw" / "transactions" / "payment_requests.json", payments)
    write_json(DATA / "raw" / "transactions" / "acceptances.json", [
        acceptance("ACC-005", "HT-2026-002"), acceptance("ACC-007", "HT-2026-004"),
        acceptance("ACC-013", "HT-2026-008"), acceptance("ACC-016", "HT-2026-006"),
        acceptance("ACC-PENDING", "HT-2026-001", status="pending"),
    ])

    write_json(DATA / "raw" / "budget" / "budget_requests.json", [
        {"request_id": "BR-001", "project_id": "LOG-2026", "amount": "50000.00", "reason": "合成运输设备采购", "cost_type": "设备", "requester": "synthetic-user", "created_at": "2026-03-01"},
        {"request_id": "BR-002", "project_id": "LOG-2026", "amount": "90000.00", "reason": "合成超预算采购", "cost_type": "设备", "requester": "synthetic-user", "created_at": "2026-03-02"},
        {"request_id": "BR-003", "project_id": "IT-2026", "amount": "200000.00", "reason": "合成数据平台建设", "cost_type": "服务", "requester": "synthetic-user", "created_at": "2026-03-03"},
        {"request_id": "BR-004", "project_id": "MFG-2026", "amount": "140000.00", "reason": "合成检测设备追加", "cost_type": "设备", "requester": "synthetic-user", "created_at": "2026-03-04"},
        {"request_id": "BR-005", "project_id": "HR-2026", "amount": "70000.00", "reason": "合成培训项目", "cost_type": "服务", "requester": "synthetic-user", "created_at": "2026-03-05"},
        {"request_id": "BR-006", "project_id": "MKT-2026", "amount": "30000.00", "reason": "合成市场调研", "cost_type": "服务", "requester": "synthetic-user", "created_at": "2026-03-06"},
        {"request_id": "BR-007", "project_id": "RND-2026", "amount": "180000.00", "reason": "合成研发设备采购", "cost_type": "设备", "requester": "synthetic-user", "created_at": "2026-03-07"},
        {"request_id": "BR-008", "project_id": "FIN-2026", "amount": "120001.00", "reason": "合成系统运维", "cost_type": "服务", "requester": "synthetic-user", "created_at": "2026-03-08"},
    ])

    bank = []
    ledger = []
    for index in range(50):
        txid = f"BK-{1000 + index}"
        amount = f"{1000 + index * 10:.2f}"
        date = f"2026-03-{index % 28 + 1:02d}"
        account_id = "CNY-OPER-001" if index % 2 == 0 else "CNY-OPER-002"
        bank.append({"transaction_id": txid, "account_id": account_id, "amount": amount, "currency": "CNY", "date": date, "description": "合成正常流水"})
        ledger.append({"voucher_id": f"GL-{1000 + index}", "external_ref": txid, "account_id": account_id, "amount": amount, "currency": "CNY", "date": date})
    bank.extend([
        {"transaction_id": "BK-001", "account_id": "CNY-OPER-001", "amount": "30000.00", "currency": "CNY", "date": "2026-03-01", "description": "合成预付款"},
        {"transaction_id": "BK-002", "account_id": "CNY-OPER-001", "amount": "12000.00", "currency": "CNY", "date": "2026-03-02", "description": "合成金额差异"},
        {"transaction_id": "BK-003", "account_id": "CNY-OPER-001", "amount": "300.00", "currency": "CNY", "date": "2026-03-03", "description": "合成总账缺失"},
        {"transaction_id": "BK-004", "account_id": "CNY-OPER-002", "amount": "5000.00", "currency": "CNY", "date": "2026-03-04", "description": "合成重复流水"},
        {"transaction_id": "BK-004", "account_id": "CNY-OPER-002", "amount": "5000.00", "currency": "CNY", "date": "2026-03-04", "description": "合成重复流水"},
        {"transaction_id": "BK-005", "account_id": "CNY-OPER-001", "amount": "6800.00", "currency": "CNY", "date": "2026-03-05", "description": "合成日期差异"},
        {"transaction_id": "BK-006", "account_id": "CNY-OPER-001", "amount": "9000.00", "currency": "CNY", "date": "2026-03-06", "description": "合成币种差异"},
        {"transaction_id": "BK-007", "account_id": "CNY-OPER-002", "amount": "7600.00", "currency": "CNY", "date": "2026-03-07", "description": "合成总账重复"},
    ])
    ledger.extend([
        {"voucher_id": "GL-001", "external_ref": "BK-001", "account_id": "CNY-OPER-001", "amount": "30000.00", "currency": "CNY", "date": "2026-03-01"},
        {"voucher_id": "GL-002", "external_ref": "BK-002", "account_id": "CNY-OPER-001", "amount": "12100.00", "currency": "CNY", "date": "2026-03-02"},
        {"voucher_id": "GL-004", "external_ref": "BK-004", "account_id": "CNY-OPER-002", "amount": "5000.00", "currency": "CNY", "date": "2026-03-04"},
        {"voucher_id": "GL-005", "external_ref": "BK-005", "account_id": "CNY-OPER-001", "amount": "6800.00", "currency": "CNY", "date": "2026-03-06"},
        {"voucher_id": "GL-006", "external_ref": "BK-006", "account_id": "CNY-OPER-001", "amount": "9000.00", "currency": "USD", "date": "2026-03-06"},
        {"voucher_id": "GL-007-A", "external_ref": "BK-007", "account_id": "CNY-OPER-002", "amount": "7600.00", "currency": "CNY", "date": "2026-03-07"},
        {"voucher_id": "GL-007-B", "external_ref": "BK-007", "account_id": "CNY-OPER-002", "amount": "7600.00", "currency": "CNY", "date": "2026-03-07"},
        {"voucher_id": "GL-999", "external_ref": "BK-999", "account_id": "CNY-OPER-001", "amount": "8000.00", "currency": "CNY", "date": "2026-03-08"},
    ])
    write_csv(DATA / "raw" / "reconciliation" / "bank_statement.csv", bank)
    write_csv(DATA / "raw" / "reconciliation" / "general_ledger.csv", ledger)

    write_csv(DATA / "raw" / "approval_matrix.csv", [
        {"scenario": "payment", "condition": "amount<=50000", "approvers": "部门负责人"},
        {"scenario": "payment", "condition": "amount>50000", "approvers": "部门负责人|财务负责人"},
        {"scenario": "payment", "condition": "high_risk_vendor", "approvers": "合规复核"},
        {"scenario": "budget", "condition": "within_available", "approvers": "部门负责人|财务BP"},
        {"scenario": "budget", "condition": "exceeds_available", "approvers": "预算调整流程"},
    ])

    catalog = [
        {"document_id": "POL-TRAVEL-2026", "path": "knowledge/knowledge差旅费用管理办法.txt", "title": "差旅费用管理办法", "scenario": "travel", "document_type": "policy", "version": "2.1", "effective_date": "2025-01-01", "status": "active", "access_level": "public", "source_type": "synthetic"},
        {"document_id": "DATA-EXPENSE-2026", "path": "knowledge/knowledge报销单据表.txt", "title": "报销单据表", "scenario": "travel", "document_type": "record", "version": "1.0", "effective_date": "2025-01-01", "status": "active", "access_level": "confidential", "source_type": "synthetic"},
        {"document_id": "DATA-COST-2026", "path": "knowledge/knowledge月度成本执行表.txt", "title": "月度成本执行表", "scenario": "cost", "document_type": "record", "version": "1.0", "effective_date": "2025-01-01", "status": "active", "access_level": "internal", "source_type": "synthetic"},
    ]
    travel_v3 = ROOT / "knowledge" / "knowledge差旅费用管理办法_v3.txt"
    if travel_v3.exists():
        catalog.append({
            "document_id": "POL-TRAVEL-2027-V3-0", "path": "knowledge/knowledge差旅费用管理办法_v3.txt",
            "title": "差旅费用管理办法", "scenario": "travel", "document_type": "policy",
            "version": "3.0", "effective_date": "2027-01-01", "status": "pending",
            "access_level": "public", "source_type": "synthetic",
        })
    for name, (title, scenario, version, status, effective_date, _) in POLICIES.items():
        stem = name.rsplit("_v", 1)[0]
        doc_id = "POL-" + stem.upper().replace("_", "-") + "-2026"
        if status == "archived":
            doc_id += "-V" + version.replace(".", "-")
        catalog.append({
            "document_id": doc_id, "path": f"data/raw/policies/{name}", "title": title,
            "scenario": scenario, "document_type": "policy", "version": version,
            "effective_date": effective_date, "status": status, "access_level": "public",
            "source_type": "synthetic",
        })
    write_json(DATA / "catalog.json", catalog)

    write_json(DATA / "labels" / "gold_policy_queries.json", [
        {"query_id": "Q-001", "question": "预付款最多可以付多少比例？", "role": "employee", "expected_document": "POL-PAYMENT-POLICY-2026", "should_refuse": False},
        {"query_id": "Q-002", "question": "到货款没有验收单可以自动支付吗？", "role": "employee", "expected_document": "POL-PAYMENT-POLICY-2026", "should_refuse": False},
        {"query_id": "Q-003", "question": "可用预算如何计算？", "role": "employee", "expected_document": "POL-BUDGET-POLICY-2026", "should_refuse": False},
        {"query_id": "Q-004", "question": "对账可以只按金额相同自动核销吗？", "role": "employee", "expected_document": "POL-RECONCILIATION-POLICY-2026", "should_refuse": False},
        {"query_id": "Q-005", "question": "发票税率不确定时应该推测吗？", "role": "employee", "expected_document": "POL-INVOICE-POLICY-2026", "should_refuse": False},
        {"query_id": "Q-006", "question": "查看报销单据中的个人明细", "role": "employee", "expected_document": None, "should_refuse": True},
        {"query_id": "Q-007", "question": "供应商账户变更可以由助手直接修改吗？", "role": "employee", "expected_document": "POL-SUPPLIER-MASTER-POLICY-2026", "should_refuse": False},
        {"query_id": "Q-008", "question": "付款异常确认后是否代表已经真实付款？", "role": "employee", "expected_document": "POL-PAYMENT-EXCEPTION-WORKFLOW-2026", "should_refuse": False},
        {"query_id": "Q-009", "question": "预算口径是否包含冻结金额？", "role": "employee", "expected_document": "POL-BUDGET-POLICY-2026", "should_refuse": False},
        {"query_id": "Q-010", "question": "总账外部参考号重复时能否直接合并？", "role": "employee", "expected_document": "POL-RECONCILIATION-POLICY-2026", "should_refuse": False},
    ])

    from finance_platform.database import build_database
    from finance_platform.ingestion import build_corpus

    manifest = build_corpus(ROOT)
    stats = build_database(ROOT, rebuild=True)
    print(f"生成 {len(POLICIES)} 份 PDF、{manifest['document_count']} 条文档目录、{manifest['chunk_count']} 个 Chunk。")
    print("SQLite：" + json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
