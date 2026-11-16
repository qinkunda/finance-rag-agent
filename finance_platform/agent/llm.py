from __future__ import annotations

import os
from typing import Any

import httpx

from ..llm_routing import model_for_question


class LLMError(RuntimeError):
    pass


class OpenAICompatibleLLM:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: float = 60.0,
                 fallback_model: str | None = None):
        env = os.getenv
        self.base_url = (base_url or env("FINANCE_LLM_BASE_URL") or env("DASHSCOPE_BASE_URL")
                         or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        self.api_key = api_key or env("FINANCE_LLM_API_KEY") or env("DASHSCOPE_API_KEY") or ""
        self.model = model or env("FINANCE_LLM_MODEL") or "qwen-plus"
        self.fallback_model = fallback_model if fallback_model is not None else env("FINANCE_LLM_FALLBACK_MODEL", "qwen-flash")
        self.timeout = timeout

    def for_question(self, question: str):
        return OpenAICompatibleLLM(
            base_url=self.base_url, api_key=self.api_key, model=model_for_question(question),
            timeout=self.timeout, fallback_model=self.fallback_model,
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.1) -> dict[str, Any]:
        try:
            return self._chat_once(messages, tools, temperature)
        except LLMError as exc:
            message = str(exc)
            recoverable = "model_not_found" in message or "Model not exist" in message or "quota" in message.lower()
            if not self.fallback_model or self.fallback_model == self.model or not recoverable:
                raise
            fallback = OpenAICompatibleLLM(
                base_url=self.base_url, api_key=self.api_key, model=self.fallback_model,
                timeout=self.timeout, fallback_model="",
            )
            result = fallback.chat(messages, tools, temperature)
            result["actual_model"] = self.fallback_model
            result["fallback_from"] = self.model
            self.model = self.fallback_model
            self.fallback_model = ""
            return result

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
