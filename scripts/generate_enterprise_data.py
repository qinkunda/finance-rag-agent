"""扩充组织、费用、合同付款、附件和对账合成数据；全部为虚构测试内容。"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def milestones() -> list[dict]:
    return [
        {"code": "advance", "name": "预付款", "ratio": "0.30", "requires": ["contract_approved", "invoice"]},
        {"code": "delivery", "name": "到货款", "ratio": "0.60", "requires": ["contract_approved", "invoice", "acceptance"]},
        {"code": "warranty", "name": "质保金", "ratio": "0.10", "requires": ["contract_approved", "warranty_expired"]},
    ]


def organization(raw: Path) -> None:
    departments = [
        {"department_id": "D-SCM", "name": "供应链部", "cost_center": "CC-SC-01"},
        {"department_id": "D-RD", "name": "研发部", "cost_center": "CC-RND-01"},
        {"department_id": "D-FIN", "name": "财务部", "cost_center": "CC-FIN-01"},
        {"department_id": "D-IT", "name": "信息部", "cost_center": "CC-IT-01"},
        {"department_id": "D-AUDIT", "name": "审计部", "cost_center": "CC-AUDIT-01"},
    ]
    roles = [
        {"role_code": "employee", "role_name": "普通员工", "knowledge_role": "employee", "data_scope": "own"},
        {"role_code": "department_manager", "role_name": "部门负责人", "knowledge_role": "manager", "data_scope": "department"},
        {"role_code": "finance_reviewer", "role_name": "财务共享审核员", "knowledge_role": "manager", "data_scope": "finance"},
        {"role_code": "finance_bp", "role_name": "财务BP", "knowledge_role": "manager", "data_scope": "finance"},
        {"role_code": "finance_manager", "role_name": "财务负责人", "knowledge_role": "admin", "data_scope": "finance"},
        {"role_code": "treasury", "role_name": "出纳", "knowledge_role": "manager", "data_scope": "finance"},
        {"role_code": "accountant", "role_name": "会计", "knowledge_role": "manager", "data_scope": "finance"},
        {"role_code": "auditor", "role_name": "审计员", "knowledge_role": "admin", "data_scope": "audit"},
        {"role_code": "admin", "role_name": "系统管理员", "knowledge_role": "employee", "data_scope": "system"},
    ]
    users = [
        ("U-EMP-001", "林晓", "D-SCM", "费用专员", "普通员工", "U-MGR-SCM", "LOG-2026", ["employee"], "active"),
        ("U-EMP-002", "陈宇", "D-RD", "研发工程师", "普通员工", "U-MGR-RD", "RND-2026", ["employee"], "active"),
        ("U-EMP-003", "周宁", "D-SCM", "采购专员", "普通员工", "U-MGR-SCM", "LOG-2026", ["employee"], "active"),
        ("U-MGR-SCM", "王衡", "D-SCM", "供应链部负责人", "经理级", "U-FIN-HEAD", "LOG-2026", ["employee", "department_manager"], "active"),
        ("U-MGR-SCM-BACK", "唐准", "D-SCM", "供应链部副负责人", "经理级", "U-FIN-HEAD", "LOG-2026", ["employee", "department_manager"], "active"),
        ("U-MGR-RD", "赵祺", "D-RD", "研发部负责人", "经理级", "U-FIN-HEAD", "RND-2026", ["employee", "department_manager"], "active"),
        ("U-FIN-REVIEW", "孙岚", "D-FIN", "共享审核员", "经理级", "U-FIN-HEAD", "FIN-2026", ["finance_reviewer"], "active"),
        ("U-FIN-REVIEW-BACK", "谢审", "D-FIN", "共享复核员", "经理级", "U-FIN-HEAD", "FIN-2026", ["finance_reviewer"], "active"),
        ("U-FIN-BP", "刘芃", "D-FIN", "财务BP", "经理级", "U-FIN-HEAD", "FIN-2026", ["finance_bp"], "active"),
        ("U-FIN-HEAD", "何谨", "D-FIN", "财务负责人", "总监级", None, "FIN-2026", ["finance_manager"], "active"),
        ("U-TREASURY", "郑安", "D-FIN", "资金出纳", "普通员工", "U-FIN-HEAD", "FIN-2026", ["treasury"], "active"),
        ("U-ACCOUNTANT", "罗会", "D-FIN", "总账会计", "普通员工", "U-FIN-HEAD", "FIN-2026", ["accountant"], "active"),
        ("U-AUDITOR", "顾言", "D-AUDIT", "审计专员", "经理级", None, "FIN-2026", ["auditor"], "active"),
        ("U-ADMIN", "系统管理员", "D-IT", "系统管理员", "普通员工", None, "IT-2026", ["admin"], "active"),
        ("U-LEAVER", "苏离", "D-SCM", "离职演示员工", "普通员工", "U-MGR-SCM", "LOG-2026", ["employee"], "terminated"),
    ]
    write_json(raw / "organization" / "departments.json", departments)
    write_json(raw / "organization" / "roles.json", roles)
    write_json(raw / "organization" / "users.json", [
        {"user_id": a, "display_name": b, "department_id": c, "position": d, "level": e,
         "manager_id": f, "project_id": g, "roles": h, "status": i,
         "effective_from": "2026-01-01", "effective_to": "2026-03-31" if i == "terminated" else None}
        for a, b, c, d, e, f, g, h, i in users
    ])


def expand_payments(raw: Path) -> None:
    vendor_path = raw / "vendors" / "vendors.json"
    vendors = read_json(vendor_path)
    additions = [
        {"vendor_id": "V-009", "vendor_name": "星汉仓储设备（合成）", "account_fingerprint": "ACCT-DEMO-009", "status": "active", "risk_level": "normal"},
        {"vendor_id": "V-010", "vendor_name": "远洲软件服务（合成）", "account_fingerprint": "ACCT-DEMO-010", "status": "active", "risk_level": "normal"},
        {"vendor_id": "V-011", "vendor_name": "科盟检测服务（合成）", "account_fingerprint": "ACCT-DEMO-011", "status": "active", "risk_level": "high"},
        {"vendor_id": "V-012", "vendor_name": "停用供应商（合成）", "account_fingerprint": "ACCT-DEMO-012", "status": "frozen", "risk_level": "normal"},
    ]
    write_json(vendor_path, vendors + [x for x in additions if x["vendor_id"] not in {v["vendor_id"] for v in vendors}])

    contract_path = raw / "contracts" / "contracts.json"
    contracts = read_json(contract_path)
    specs = [
        ("HT-2026-009", "V-009", "LOG-2026", "仓储扫码设备采购合同（合成）", "180000.00"),
        ("HT-2026-010", "V-010", "IT-2026", "数据平台运维服务合同（合成）", "450000.00"),
        ("HT-2026-011", "V-009", "FIN-2026", "财务档案整理服务合同（合成）", "60000.00"),
        ("HT-2026-012", "V-011", "RND-2026", "研发环境检测服务合同（合成）", "210000.00"),
    ]
    existing = {x["contract_id"] for x in contracts}
    for contract_id, vendor, project, title, amount in specs:
        if contract_id not in existing:
            contracts.append({"contract_id": contract_id, "vendor_id": vendor, "project_id": project,
                              "title": title, "total_amount": amount, "currency": "CNY", "approved": True,
                              "status": "active", "signed_at": "2026-03-15", "milestones": milestones()})
    write_json(contract_path, contracts)

    payment_path = raw / "transactions" / "payment_requests.json"
    payments = read_json(payment_path)
    invoice_path = raw / "transactions" / "invoices.json"
    invoices = read_json(invoice_path)
    acceptance_path = raw / "transactions" / "acceptances.json"
    acceptances = read_json(acceptance_path)
    payment_specs = [
        ("PR-017", "HT-2026-009", "advance", "54000.00", "INV-018", None, False, "ACCT-DEMO-009"),
        ("PR-018", "HT-2026-009", "delivery", "108000.00", "INV-019", "ACC-018", False, "ACCT-DEMO-009"),
        ("PR-019", "HT-2026-010", "advance", "135000.00", "INV-020", None, False, "ACCT-DEMO-010"),
        ("PR-020", "HT-2026-010", "delivery", "270000.00", "INV-021", "ACC-020", False, "ACCT-DEMO-010"),
        ("PR-021", "HT-2026-011", "advance", "18000.00", "INV-022", None, False, "ACCT-DEMO-009"),
        ("PR-022", "HT-2026-011", "delivery", "36000.00", "INV-023", "ACC-022", False, "ACCT-DEMO-009"),
        ("PR-023", "HT-2026-012", "advance", "63000.00", "INV-024", None, False, "ACCT-DEMO-011"),
        ("PR-024", "HT-2026-012", "warranty", "21000.00", None, None, True, "ACCT-DEMO-011"),
    ]
    existing_p = {x["request_id"] for x in payments}
    for request_id, contract, milestone, amount, invoice, acceptance, warranty, account in payment_specs:
        if request_id not in existing_p:
            payments.append({"request_id": request_id, "contract_id": contract, "milestone": milestone,
                             "amount": amount, "invoice_id": invoice, "acceptance_id": acceptance,
                             "warranty_expired": warranty, "account_fingerprint": account,
                             "requester": "U-EMP-003", "created_at": "2026-03-16T09:00:00+08:00"})
    existing_i = {x["invoice_id"] for x in invoices}
    for request_id, _, _, amount, invoice, _, _, _ in payment_specs:
        if invoice and invoice not in existing_i:
            invoices.append({"invoice_id": invoice, "request_id": request_id, "invoice_number": "SYN-" + invoice,
                             "status": "valid", "amount": amount, "tax_rate": "0.06", "issue_date": "2026-03-15"})
    existing_a = {x["acceptance_id"] for x in acceptances}
    for _, contract, _, _, _, acceptance, _, _ in payment_specs:
        if acceptance and acceptance not in existing_a:
            acceptances.append({"acceptance_id": acceptance, "contract_id": contract, "milestone": "delivery",
                                "status": "approved", "accepted_at": "2026-03-15"})
    write_json(payment_path, payments)
    write_json(invoice_path, invoices)
    write_json(acceptance_path, acceptances)

    workflows = [
        {"request_id": "PR-001", "title": "仓储服务预付款审批", "status": "pending_finance_review", "department_id": "D-SCM", "project_id": "LOG-2026", "amount": "30000.00", "created_by": "U-EMP-003", "created_at": "2026-03-12T09:00:00+08:00"},
        {"request_id": "PR-002", "title": "到货款缺少验收材料", "status": "needs_supplement", "department_id": "D-SCM", "project_id": "LOG-2026", "amount": "60000.00", "created_by": "U-EMP-003", "requires_finance_manager": True, "created_at": "2026-03-12T09:05:00+08:00", "exception": {"code": "missing_acceptance", "message": "缺少验收单，请补充验收材料"}},
        {"request_id": "PR-003", "title": "预付款金额与里程碑不一致", "status": "needs_supplement", "department_id": "D-SCM", "project_id": "LOG-2026", "amount": "70000.00", "created_by": "U-EMP-003", "requires_finance_manager": True, "created_at": "2026-03-12T09:10:00+08:00", "exception": {"code": "milestone_amount_mismatch", "message": "申请金额与合同里程碑不一致"}},
        {"request_id": "PR-007", "title": "供应商账户指纹不一致", "status": "needs_supplement", "department_id": "D-SCM", "project_id": "MFG-2026", "amount": "90000.00", "created_by": "U-EMP-003", "requires_finance_manager": True, "created_at": "2026-03-12T09:15:00+08:00", "exception": {"code": "vendor_account_mismatch", "message": "收款账户与供应商主数据不一致，需独立复核"}},
    ]
    write_json(raw / "transactions" / "payment_workflows.json", workflows)

    from generate_demo_corpus import write_pdf
    attachments = []
    for invoice in invoices[-7:]:
        path = raw / "attachments" / f"{invoice['invoice_id']}.pdf"
        write_pdf(path, f"合成发票 {invoice['invoice_number']}", [
            f"发票号码：{invoice['invoice_number']}", f"关联申请：{invoice['request_id']}",
            f"含税金额：{invoice['amount']} 元", f"税率：{invoice['tax_rate']}",
            "本影像仅用于字段关联和退补演示，不是真实税务发票。",
        ])
        attachments.append({"attachment_id": "ATT-" + invoice["invoice_id"], "record_id": invoice["request_id"],
                            "type": "invoice", "path": str(path.relative_to(ROOT)),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    for acceptance in acceptances[-3:]:
        path = raw / "attachments" / f"{acceptance['acceptance_id']}.pdf"
        write_pdf(path, f"合成验收单 {acceptance['acceptance_id']}", [
            f"关联合同：{acceptance['contract_id']}", f"里程碑：{acceptance['milestone']}",
            f"验收状态：{acceptance['status']}", "本附件仅用于合成业务演示。",
        ])
        attachments.append({"attachment_id": "ATT-" + acceptance["acceptance_id"], "record_id": acceptance["contract_id"],
                            "type": "acceptance", "path": str(path.relative_to(ROOT)),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    write_json(raw / "attachments" / "attachments.json", attachments)


def expenses(raw: Path) -> None:
    statuses = ["closed", "closed", "closed", "closed", "closed", "closed", "paid", "paid",
                "pending_payment", "pending_payment", "pending_finance_review", "pending_finance_review",
                "pending_department", "pending_department", "needs_supplement", "needs_supplement",
                "pending_finance_manager", "pending_budget"]
    creators = ["U-EMP-001", "U-EMP-002", "U-EMP-003"]
    cities = ["深圳", "北京", "成都", "杭州", "长沙", "上海"]
    reports = []
    for index, status in enumerate(statuses, 1):
        creator = creators[(index - 1) % len(creators)]
        department = "D-RD" if creator == "U-EMP-002" else "D-SCM"
        project = "RND-2026" if creator == "U-EMP-002" else "LOG-2026"
        days = 2 + index % 4
        price = 450 + (index % 4) * 50
        nights = max(days - 1, 1)
        lodging = price * nights
        meal = days * (150 if cities[index % 6] in {"深圳", "北京", "上海"} else 120)
        transport = days * 80
        total = lodging + meal + transport
        overlimit = max(price - 500, 0) * nights if cities[index % 6] in {"深圳", "北京", "上海"} else 0
        report = {"report_id": f"BX-2026-{index:03d}", "city": cities[index % 6], "days": days,
                  "level": "普通员工", "lodging_price": f"{price:.2f}", "lodging_total": f"{lodging:.2f}",
                  "meal_total": f"{meal:.2f}", "transport_total": f"{transport:.2f}",
                  "total_amount": f"{total:.2f}", "overlimit_amount": f"{overlimit:.2f}",
                  "approval_chain": "部门负责人|财务共享审核", "created_at": f"2026-03-{index:02d}T09:00:00+08:00",
                  "status": status, "department_id": department, "project_id": project, "created_by": creator}
        if status == "needs_supplement":
            report["exception"] = {"code": "missing_expense_attachment", "message": "缺少住宿发票或行程材料", "status": "open"}
        elif status == "pending_finance_manager":
            report["exception"] = {"code": "lodging_overlimit", "message": "住宿超过标准，需财务负责人复核", "status": "pending_verification"}
        reports.append(report)
    write_json(raw / "expenses" / "expense_reports.json", reports)


def expand_reconciliation(raw: Path) -> None:
    bank_path = raw / "reconciliation" / "bank_statement.csv"
    ledger_path = raw / "reconciliation" / "general_ledger.csv"
    bank = read_csv(bank_path)
    ledger = read_csv(ledger_path)
    existing = {x["transaction_id"] for x in bank}
    for number in range(100, 162):
        txid = f"BK-{number}"
        if txid in existing:
            continue
        account = "CNY-OPER-001" if number % 2 == 0 else "CNY-OPER-002"
        amount = f"{1000 + number * 37}.00"
        date = f"2026-04-{1 + (number - 100) % 28:02d}"
        bank.append({"transaction_id": txid, "account_id": account, "amount": amount, "currency": "CNY",
                     "date": date, "description": f"合成日常结算 {number}"})
        ledger.append({"voucher_id": f"GL-{number}", "external_ref": txid, "account_id": account,
                       "amount": amount, "currency": "CNY", "date": date})
    write_csv(bank_path, bank)
    write_csv(ledger_path, ledger)


def main() -> None:
    raw = ROOT / "data" / "raw"
    organization(raw)
    expand_payments(raw)
    expenses(raw)
    expand_reconciliation(raw)
    print("ENTERPRISE_SYNTHETIC_DATA_OK")


if __name__ == "__main__":
    main()
