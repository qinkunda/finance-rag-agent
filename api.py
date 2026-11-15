"""统一财务平台：一个 Web 工作台、一个企业会话入口，保留旧只读契约。"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from finance_platform.changes import ChangeService
from finance_platform.database import connect
from finance_platform.enterprise_database import EnterpriseRepository
from finance_platform.graph import FinanceGraphService
from finance_platform.orchestrator import UnifiedFinancePlatform
from finance_platform.vector_rag import vector_directory
from finance_platform.workflow import WorkflowConflictError, WorkflowService

ROOT = Path(__file__).resolve().parent
STATIC_INDEX = ROOT / "static" / "index.html"
app = FastAPI(title="星汉财务统一智能平台（合成演示）", version="0.4.0")


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    user_role: str = "employee"
    session_id: str = Field(default="default", min_length=1, max_length=100)


class AgentExecuteRequest(BaseModel):
    scenario: str
    payload: dict[str, Any] = Field(default_factory=dict)
    user_role: str = "employee"


class SessionRequest(BaseModel):
    user_id: str
    session_id: str | None = Field(default=None, max_length=100)


class EnterpriseQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    session_id: str = Field(min_length=1, max_length=100)


class ExpenseCreateRequest(BaseModel):
    session_id: str
    city: str
    days: int = Field(ge=1, le=30)
    lodging_price: str


class PaymentSubmitRequest(BaseModel):
    session_id: str
    request_id: str


class SessionOnlyRequest(BaseModel):
    session_id: str


class WorkflowActionRequest(BaseModel):
    session_id: str
    action: str
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=100)
    comment: str = Field(default="", max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict)


class ReconciliationRunRequest(BaseModel):
    session_id: str
    account_id: str | None = None


class ChangeRequest(BaseModel):
    session_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class DocumentPublishRequest(BaseModel):
    session_id: str
    effective_from: str
    status: str = "active"


@lru_cache
def get_platform() -> UnifiedFinancePlatform:
    return UnifiedFinancePlatform()


@lru_cache
def get_workflow() -> WorkflowService:
    return WorkflowService(db_path=get_platform().data.db_path)


@lru_cache
def get_changes() -> ChangeService:
    return ChangeService(db_path=get_platform().data.db_path)


@lru_cache
def get_graph() -> FinanceGraphService:
    return FinanceGraphService(db_path=get_platform().data.db_path)


def repository() -> EnterpriseRepository:
    return get_workflow().repository


@app.exception_handler(PermissionError)
async def permission_error(_, exc: PermissionError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(WorkflowConflictError)
async def conflict_error(_, exc: WorkflowConflictError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(KeyError)
async def not_found_error(_, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def value_error(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def unified_workspace() -> FileResponse:
    return FileResponse(STATIC_INDEX)


@app.get("/health")
def health() -> dict[str, Any]:
    platform = get_platform()
    manifest_path = ROOT / "data" / "curated" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    offline = os.getenv("FINANCE_OFFLINE_DEMO", "true").lower() == "true"
    return {
        "status": "ok", "offline_demo": offline, "retrieval_mode": platform.policy.mode,
        "document_count": manifest["document_count"], "chunk_count": manifest["chunk_count"],
        "vector_index_ready": (vector_directory(ROOT) / "chroma.sqlite3").exists(),
        "database": platform.repository.stats(), "data_declaration": "全部为企业财务合成演示数据",
    }


@app.post("/agent")
def agent_api(request: QueryRequest) -> dict[str, Any]:
    try:
        return get_platform().handle(request.question, request.user_role, request.session_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/ask", include_in_schema=False)
def ask_api(request: QueryRequest) -> dict[str, Any]:
    try:
        return get_platform().policy.answer(request.question, request.user_role)
    except (ValueError, KeyError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/agent/execute", include_in_schema=False)
def agent_execute_api(request: AgentExecuteRequest) -> dict[str, Any]:
    try:
        return get_platform().execute(request.scenario, request.payload, request.user_role)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/enterprise/users")
def enterprise_users() -> list[dict[str, Any]]:
    return repository().list_users()


@app.post("/enterprise/session")
def enterprise_session(request: SessionRequest) -> dict[str, Any]:
    return repository().create_session(request.user_id, request.session_id)


@app.get("/enterprise/workspace")
def enterprise_workspace(session_id: str) -> dict[str, Any]:
    identity = repository().identity(session_id)
    visible = repository().list_work_items(identity, "visible")
    tasks = repository().list_work_items(identity, "tasks")
    requests = repository().list_work_items(identity, "requests")
    with connect(get_platform().data.db_path) as conn:
        available_payments = [dict(x) for x in conn.execute(
            """SELECT p.request_id,p.contract_id,p.amount,p.created_at,c.title AS contract_title
               FROM payment_requests p JOIN contracts c ON c.contract_id=p.contract_id
               LEFT JOIN work_items w ON w.scenario='payment' AND w.record_id=p.request_id
               WHERE p.requester=? AND w.item_id IS NULL ORDER BY p.request_id""", (identity["user_id"],)
        )]
        open_exceptions = 0
        for item in visible:
            open_exceptions += conn.execute(
                "SELECT COUNT(*) FROM exceptions WHERE item_id=? AND status NOT IN ('resolved','closed')", (item["item_id"],)
            ).fetchone()[0]
    return {"identity": identity, "visible_items": visible, "tasks": tasks, "requests": requests,
            "available_payments": available_payments,
            "summary": {"visible": len(visible), "tasks": len(tasks), "requests": len(requests),
                        "open_exceptions": open_exceptions}}


@app.get("/enterprise/items/{item_id}")
def enterprise_item(item_id: str, session_id: str) -> dict[str, Any]:
    identity = repository().identity(session_id)
    item = repository().get_work_item(item_id, identity)
    item["allowed_actions"] = get_workflow().allowed_actions(identity, item)
    queries = {"expense": "差旅 住宿标准 审批 退补", "payment": "合同付款 发票 验收 供应商 预算",
               "reconciliation": "银行对账 差异 复核 关闭"}
    hits = get_platform().policy.search(queries.get(item["scenario"], "财务制度"), identity["knowledge_role"], top_k=2)
    item["current_policy_citations"] = get_platform().policy._citations(hits)
    return item


@app.post("/enterprise/expenses")
def create_expense(request: ExpenseCreateRequest) -> dict[str, Any]:
    return get_workflow().create_expense(request.session_id, request.model_dump())


@app.post("/enterprise/payments/{request_id}/submit")
def submit_payment(request_id: str, request: SessionOnlyRequest) -> dict[str, Any]:
    return get_workflow().submit_payment(request.session_id, request_id)


@app.post("/enterprise/items/{item_id}/actions")
def item_action(item_id: str, request: WorkflowActionRequest) -> dict[str, Any]:
    return get_workflow().act(request.session_id, item_id, request.action,
                              expected_version=request.expected_version, idempotency_key=request.idempotency_key,
                              comment=request.comment, payload=request.payload)


@app.post("/enterprise/reconciliation/runs")
def reconciliation_run(request: ReconciliationRunRequest) -> dict[str, Any]:
    result = get_workflow().run_reconciliation(request.session_id, request.account_id)
    get_platform().refresh()
    return result


@app.post("/enterprise/assistant")
def enterprise_assistant(request: EnterpriseQueryRequest) -> dict[str, Any]:
    return get_platform().handle_enterprise(request.question, request.session_id)


@app.get("/enterprise/graph")
def enterprise_graph(session_id: str, record_id: str | None = None, depth: int = 4) -> dict[str, Any]:
    return get_graph().query(session_id, record_id, depth)


@app.get("/enterprise/admin-data")
def enterprise_admin_data(session_id: str) -> dict[str, Any]:
    identity = repository().identity(session_id)
    if not set(identity["roles"]) & {"admin", "finance_manager", "auditor"}:
        raise PermissionError("当前角色无权查看变更管理")
    with connect(get_platform().data.db_path) as conn:
        return {
            "users": repository().list_users(),
            "vendors": [dict(x) for x in conn.execute("SELECT * FROM vendors ORDER BY vendor_id")],
            "budgets": [dict(x) for x in conn.execute("SELECT * FROM budgets ORDER BY project_id")],
            "documents": [dict(x) for x in conn.execute("SELECT * FROM document_states ORDER BY title,document_id")],
            "changes": [dict(x) for x in conn.execute("SELECT * FROM change_events ORDER BY created_at DESC LIMIT 30")],
        }


@app.post("/enterprise/changes/users/{user_id}/deactivate")
def deactivate_user(user_id: str, request: ChangeRequest) -> dict[str, Any]:
    return get_changes().deactivate_user(request.session_id, user_id, request.payload.get("replacement_id"))


@app.post("/enterprise/changes/users/{user_id}")
def change_user(user_id: str, request: ChangeRequest) -> dict[str, Any]:
    return get_changes().change_user(request.session_id, user_id, request.payload)


@app.post("/enterprise/changes/vendors/{vendor_id}")
def change_vendor(vendor_id: str, request: ChangeRequest) -> dict[str, Any]:
    result = get_changes().change_vendor(request.session_id, vendor_id, request.payload)
    get_platform().refresh()
    return result


@app.post("/enterprise/changes/budgets/{project_id}")
def adjust_budget(project_id: str, request: ChangeRequest) -> dict[str, Any]:
    result = get_changes().adjust_budget(request.session_id, project_id,
                                         str(request.payload.get("annual_budget", "")),
                                         str(request.payload.get("reason", "")))
    get_platform().refresh()
    return result


@app.post("/enterprise/changes/documents/{document_id}")
def publish_document(document_id: str, request: DocumentPublishRequest) -> dict[str, Any]:
    return get_changes().publish_document(request.session_id, document_id, request.effective_from, request.status)


@app.get("/enterprise/changes")
def list_changes(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    return get_changes().list_changes(session_id, limit)
