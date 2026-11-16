from __future__ import annotations

import os
from typing import Any

import httpx

from ..llm_routing import model_for_question

# 内置兜底链：主模型不可用（免费额度用尽/未开启/限流）时按顺序降级。
# 速度优先：主力 qwen-flash（快）；qwen3.7-max 作出错兜底；思维模型（qwen4-max）响应慢且易漏思维残渣，不进链。
DEFAULT_FALLBACK_MODELS = ["qwen3.7-max-2026-06-08", "qwen-flash"]


class LLMError(RuntimeError):
    pass


class OpenAICompatibleLLM:
    """OpenAI 兼容客户端，支持按问题路由（for_question）与多档模型降级链。

    降级触发条件（recoverable）：模型不存在 / 免费额度用尽 / quota 类错误。
    鉴权失败等不可恢复错误直接抛出，不浪费降级尝试。
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: float = 60.0,
                 fallback_model: str | None = None,
                 fallback_models: list[str] | None = None):
        env = os.getenv
        self.base_url = (base_url or env("FINANCE_LLM_BASE_URL") or env("DASHSCOPE_BASE_URL")
                         or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        self.api_key = api_key or env("FINANCE_LLM_API_KEY") or env("DASHSCOPE_API_KEY") or ""
        self.model = model or env("FINANCE_LLM_MODEL") or "qwen-plus"
        self.timeout = timeout
        self.last_error = ""

        if fallback_models is not None:
            chain = list(fallback_models)
        else:
            chain = []
            # 兼容旧参数：单档 fallback_model
            legacy = fallback_model if fallback_model is not None else env("FINANCE_LLM_FALLBACK_MODEL", "")
            if legacy:
                chain.append(legacy)
            # 环境变量可追加自定义降级档（逗号分隔）
            chain.extend(s.strip() for s in env("FINANCE_LLM_FALLBACK_MODELS", "").split(",") if s.strip())
            # 内置兜底链
            chain.extend(DEFAULT_FALLBACK_MODELS)
        self.fallback_models = [m for m in dict.fromkeys(chain) if m and m != self.model]

    def for_question(self, question: str):
        return OpenAICompatibleLLM(
            base_url=self.base_url, api_key=self.api_key, model=model_for_question(question),
            timeout=self.timeout, fallback_models=list(self.fallback_models),
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.1) -> dict[str, Any]:
        try:
            return self._chat_once(messages, tools, temperature)
        except LLMError as exc:
            message = str(exc)
            recoverable = ("model_not_found" in message or "Model not exist" in message
                           or "quota" in message.lower())
            if not recoverable or not self.fallback_models:
                raise
            from_model = self.model
            last_exc: LLMError = exc
            for candidate in list(self.fallback_models):
                self.model = candidate
                try:
                    result = self._chat_once(messages, tools, temperature)
                except LLMError as sub_exc:
                    self.last_error = f"{candidate}: {sub_exc}"
                    last_exc = sub_exc
                    continue
                result["actual_model"] = candidate
                result["fallback_from"] = from_model
                self.last_error = f"{from_model}: {exc}"
                self.fallback_models = []
                return result
            raise last_exc

    def _chat_once(self, messages: list[dict], tools: list[dict] | None,
                   temperature: float) -> dict[str, Any]:
        if not self.api_key:
            raise LLMError("未配置 LLM API Key")
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            payload["tools"] = tools
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM 网关连接失败：{exc}") from exc
        if response.status_code != 200:
            raise LLMError(f"LLM 网关返回 {response.status_code}：{response.text[:300]}")
        body = response.json()
        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        normalized_calls = []
        for call in msg.get("tool_calls") or []:
            normalized_calls.append({
                "id": call.get("id", ""), "type": "function",
                "function": {
                    "name": (call.get("function") or {}).get("name", ""),
                    "arguments": (call.get("function") or {}).get("arguments") or "{}",
                },
            })
        return {"content": msg.get("content"), "tool_calls": normalized_calls,
                "finish_reason": choice.get("finish_reason", "stop")}
