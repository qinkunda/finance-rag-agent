"""生成可复现的合成财务语料；不读取任何本机业务文件或密钥。"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SYNTHETIC = "本文件为星汉集团合成示例，仅用于软件测试，不对应任何真实企业、人员或交易。"

POLICIES = {
    "payment_policy_v1_0.pdf": (
        "付款管理办法",
        [
            "第一条 付款申请必须关联已审批合同、供应商主数据和预算项目，缺一不可。",
            "第二条 预付款比例不得超过合同含税总额的百分之三十；超过时须提交财务负责人专项审批。",
            "第三条 到货款支付前必须取得验收单和合法有效的发票；未取得验收单时系统只能转人工复核。",
            "第四条 质保金比例为合同含税总额的百分之十，质保期满且无未决质量问题后方可申请。",
            "第五条 收款账户必须与供应商主数据一致；账户变更须由财务经理线下复核，本系统不自动修改。",
            "第六条 单笔付款超过五万元时，审批链在部门负责人之后增加财务负责人节点。",
            "第七条 本系统只执行预检和生成模拟工单，不自动付款、不自动记账。",
            "第八条 本办法版本为V1.0，自2026年1月1日起生效。",
        ],
    ),
    "contract_review_guide_v1_0.pdf": (
        "合同财务条款审核指引",
        [
            "第一条 审核范围包括合同金额、税率、付款里程碑、验收条件、发票要求、质保金和收款账户。",
            "第二条 付款里程碑必须使用可验证的事件描述，不得只写适时付款或按甲方要求付款。",
            "第三条 合同条款与结构化字段不一致时，以已签署合同原文为证据并转人工复核，不得静默覆盖。",
            "第四条 预付款、到货款和质保金比例合计不得超过合同总额的百分之一百。",
            "第五条 对缺少税率或发票类型的合同，审核结论必须为待补充，不得推测税务处理。",
            "第六条 本指引版本为V1.0，自2026年1月1日起生效。",
        ],
    ),
    "budget_policy_v1_0.pdf": (
        "预算管理与调整办法",
        [
            "第一条 可用预算等于年度预算减去已承诺金额和已发生金额，计算由规则程序完成。",
            "第二条 申请金额不超过可用预算时，可进入部门负责人和财务BP审批。",
            "第三条 申请金额超过可用预算时，不得直接提交付款，应先发起预算调整或削减申请金额。",
            "第四条 预算调整必须写明来源项目、目标项目、调整金额和业务原因，并保持总预算平衡。",
            "第五条 普通员工可以查看公开预算制度；部门预算明细仅向经理和管理员角色开放。",
            "第六条 本办法版本为V1.0，自2026年2月1日起生效。",
        ],
    ),
    "reconciliation_policy_v1_0.pdf": (
        "财务对账与差异处理规范",
        [
            "第一条 对账以银行交易流水号和总账外部参考号进行精确匹配，不得仅凭金额相同自动核销。",
            "第二条 已匹配但金额不一致的记录标记为金额差异，必须由会计人员复核。",
            "第三条 银行有记录而总账无记录的，标记为总账缺失；总账有记录而银行无记录的，标记为银行未达。",
            "第四条 同一银行流水号出现多次时标记为重复记录，不得自动汇总后掩盖差异。",
            "第五条 差异处理结果必须保留原始记录标识、差异类型和处理状态，支持审计追溯。",
            "第六条 本规范版本为V1.0，自2026年3月1日起生效。",
        ],
    ),
    "invoice_policy_v1_0.pdf": (
        "发票与税务资料管理细则",
        [
            "第一条 发票号码、开票日期、销售方、购买方、金额和税额应按结构化字段保存。",
            "第二条 发票影像与结构化字段不一致时，状态设为待复核，不得直接用于自动付款。",
            "第三条 税率和税收政策的判断必须记录政策来源及生效日期；知识库没有依据时应拒绝推测。",
            "第四条 本细则不提供更广泛税务意见，具体税务处理由具备资质的人员复核。",
            "第五条 本细则版本为V1.0，自2026年1月15日起生效。",
        ],
    ),
    "shared_service_workflow_v1_0.pdf": (
        "财务共享工单流转规则",
        [
            "第一条 工单类型包括费用报销、付款预检、预算调整、对账差异和制度咨询。",
            "第二条 知识查询和只读预检可以直接返回；创建工单必须获得用户明确确认。",
            "第三条 缺少合同、验收单、发票或预算依据时，工单进入待补充状态并列出缺口。",
            "第四条 权限不足、规则冲突和模型输出校验失败时，必须转人工，不得自动放行。",
            "第五条 审计日志记录用户角色、场景、工具、结果和规则版本，不记录完整敏感单据。",
            "第六条 本规则版本为V1.0，自2026年3月1日起生效。",
        ],
    ),
}


def write_pdf(path: Path, title: str, clauses: list[str]) -> None:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    c = canvas.Canvas(str(path), pagesize=A4)
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


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    for name, (title, clauses) in POLICIES.items():
        write_pdf(DATA / "raw" / "policies" / name, title, clauses)

    write_json(DATA / "raw" / "contracts" / "contracts.json", [
        {
            "contract_id": "HT-2026-001", "vendor_id": "V-001", "total_amount": "100000.00",
            "currency": "CNY", "approved": True, "status": "active",
            "milestones": [
                {"code": "advance", "name": "预付款", "ratio": "0.30", "requires": ["contract_approved", "invoice"]},
                {"code": "delivery", "name": "到货款", "ratio": "0.60", "requires": ["contract_approved", "invoice", "acceptance"]},
                {"code": "warranty", "name": "质保金", "ratio": "0.10", "requires": ["contract_approved", "warranty_expired"]},
            ],
        }
    ])
    write_json(DATA / "raw" / "transactions" / "payment_requests.json", [
        {"request_id": "PR-001", "contract_id": "HT-2026-001", "milestone": "advance", "amount": "30000.00", "invoice": True, "acceptance": False, "warranty_expired": False, "account_matches_vendor": True},
        {"request_id": "PR-002", "contract_id": "HT-2026-001", "milestone": "delivery", "amount": "60000.00", "invoice": True, "acceptance": False, "warranty_expired": False, "account_matches_vendor": True},
        {"request_id": "PR-003", "contract_id": "HT-2026-001", "milestone": "advance", "amount": "70000.00", "invoice": True, "acceptance": False, "warranty_expired": False, "account_matches_vendor": True},
    ])
    write_json(DATA / "raw" / "vendors" / "vendors.json", [
        {"vendor_id": "V-001", "vendor_name": "华南设备供应商（合成）", "account_fingerprint": "ACCT-DEMO-001", "status": "active"}
    ])
    write_csv(DATA / "raw" / "budget" / "budgets.csv", [
        {"department": "供应链部", "project_id": "LOG-2026", "annual_budget": "200000.00", "committed": "80000.00", "actual": "50000.00"},
        {"department": "信息部", "project_id": "IT-2026", "annual_budget": "150000.00", "committed": "30000.00", "actual": "20000.00"},
    ])
    write_json(DATA / "raw" / "budget" / "budget_requests.json", [
        {"request_id": "BR-001", "project_id": "LOG-2026", "amount": "50000.00", "reason": "合成运输设备采购"},
        {"request_id": "BR-002", "project_id": "LOG-2026", "amount": "90000.00", "reason": "合成超预算采购"},
    ])
    write_csv(DATA / "raw" / "reconciliation" / "bank_statement.csv", [
        {"transaction_id": "BK-001", "amount": "30000.00", "date": "2026-03-01", "description": "合成预付款"},
        {"transaction_id": "BK-002", "amount": "12000.00", "date": "2026-03-02", "description": "合成服务费"},
        {"transaction_id": "BK-003", "amount": "300.00", "date": "2026-03-03", "description": "合成银行手续费"},
        {"transaction_id": "BK-004", "amount": "5000.00", "date": "2026-03-04", "description": "合成重复记录"},
        {"transaction_id": "BK-004", "amount": "5000.00", "date": "2026-03-04", "description": "合成重复记录"},
    ])
    write_csv(DATA / "raw" / "reconciliation" / "general_ledger.csv", [
        {"voucher_id": "GL-001", "external_ref": "BK-001", "amount": "30000.00", "date": "2026-03-01"},
        {"voucher_id": "GL-002", "external_ref": "BK-002", "amount": "12100.00", "date": "2026-03-02"},
        {"voucher_id": "GL-003", "external_ref": "BK-999", "amount": "8000.00", "date": "2026-03-05"},
    ])
    write_csv(DATA / "raw" / "approval_matrix.csv", [
        {"scenario": "payment", "condition": "amount<=50000", "approvers": "部门负责人"},
        {"scenario": "payment", "condition": "amount>50000", "approvers": "部门负责人|财务负责人"},
        {"scenario": "budget", "condition": "within_available", "approvers": "部门负责人|财务BP"},
        {"scenario": "budget", "condition": "exceeds_available", "approvers": "预算调整流程"},
    ])

    catalog = [
        {"document_id":"POL-TRAVEL-2026","path":"knowledge/knowledge差旅费用管理办法.txt","title":"差旅费用管理办法","scenario":"travel","document_type":"policy","version":"2.1","effective_date":"2025-01-01","status":"active","access_level":"public","source_type":"synthetic"},
        {"document_id":"DATA-EXPENSE-2026","path":"knowledge/knowledge报销单据表.txt","title":"报销单据表","scenario":"travel","document_type":"record","version":"1.0","effective_date":"2025-01-01","status":"active","access_level":"confidential","source_type":"synthetic"},
        {"document_id":"DATA-COST-2026","path":"knowledge/knowledge月度成本执行表.txt","title":"月度成本执行表","scenario":"cost","document_type":"record","version":"1.0","effective_date":"2025-01-01","status":"active","access_level":"internal","source_type":"synthetic"},
    ]
    for name, (title, _) in POLICIES.items():
        stem = name.rsplit("_v", 1)[0]
        scenario = {
            "payment_policy":"payment", "contract_review_guide":"payment", "budget_policy":"budget",
            "reconciliation_policy":"reconciliation", "invoice_policy":"invoice", "shared_service_workflow":"workflow",
        }[stem]
        doc_id = "POL-" + stem.upper().replace("_", "-") + "-2026"
        catalog.append({"document_id":doc_id,"path":f"data/raw/policies/{name}","title":title,"scenario":scenario,"document_type":"policy","version":"1.0","effective_date":"2026-01-01","status":"active","access_level":"public","source_type":"synthetic"})
    write_json(DATA / "catalog.json", catalog)

    write_json(DATA / "labels" / "gold_policy_queries.json", [
        {"query_id":"Q-001","question":"预付款最多可以付多少比例？","role":"employee","expected_document":"POL-PAYMENT-POLICY-2026","should_refuse":False},
        {"query_id":"Q-002","question":"到货款没有验收单可以自动支付吗？","role":"employee","expected_document":"POL-PAYMENT-POLICY-2026","should_refuse":False},
        {"query_id":"Q-003","question":"可用预算如何计算？","role":"employee","expected_document":"POL-BUDGET-POLICY-2026","should_refuse":False},
        {"query_id":"Q-004","question":"对账可以只按金额相同自动核销吗？","role":"employee","expected_document":"POL-RECONCILIATION-POLICY-2026","should_refuse":False},
        {"query_id":"Q-005","question":"发票税率不确定时应该推测吗？","role":"employee","expected_document":"POL-INVOICE-POLICY-2026","should_refuse":False},
        {"query_id":"Q-006","question":"查看报销单据中的个人明细","role":"employee","expected_document":None,"should_refuse":True},
    ])
    print(f"生成 {len(POLICIES)} 份 PDF、{len(catalog)} 条文档目录及配套合成业务数据。")


if __name__ == "__main__":
    main()
