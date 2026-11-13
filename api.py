"""FastAPI 入口：/ask 保留原 RAG 接口契约，/agent 提供多场景离线演示。"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from finance_platform.multi_agent import Supervisor

app = FastAPI(title="Finance RAG & Multi-Agent Demo", version="0.2.0")


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    user_role: str = "employee"


class AgentExecuteRequest(BaseModel):
    scenario: str
    payload: dict[str, Any] = Field(default_factory=dict)
    user_role: str = "employee"


@lru_cache
def get_supervisor() -> Supervisor:
    return Supervisor()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "offline_demo": os.getenv("FINANCE_OFFLINE_DEMO", "true").lower() == "true"}


@app.post("/ask")
def ask_api(request: QueryRequest) -> dict[str, Any]:
    offline = os.getenv("FINANCE_OFFLINE_DEMO", "true").lower() == "true"
    try:
        if offline:
            return get_supervisor().agents["policy"].run({"question": request.question}, request.user_role)
        # 延迟导入：没有 API Key 时，离线接口和规则测试仍可运行。
        from main import ask
        return ask(request.question, user_role=request.user_role)
    except (ValueError, KeyError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/agent")
def agent_api(request: QueryRequest) -> dict[str, Any]:
    try:
        return get_supervisor().handle_text(request.question, request.user_role)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/agent/execute")
def agent_execute_api(request: AgentExecuteRequest) -> dict[str, Any]:
    try:
        return get_supervisor().execute(request.scenario, request.payload, request.user_role)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
