# api.py — RAG 的 HTTP 服务入口，供 Dify 工作流 HTTP 节点调用
# 复用 main.py 的 ask()（权限过滤/限流/审计全保留）
from fastapi import FastAPI
from pydantic import BaseModel
from main import ask
from guard import sanitize_prompt, validate_output

app = FastAPI(title="Finance RAG Agent API", version="1.0")

class AskRequest(BaseModel):
    question: str
    user_role: str = "employee"

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ask")
def ask_api(req: AskRequest):
    safe_q = sanitize_prompt(req.question)
    result = ask(safe_q, user_role=req.user_role)
    if result.get("blocked"):
        return {"answer": result["answer"], "blocked": True}
    return {"answer": validate_output(result["answer"]), "blocked": False}