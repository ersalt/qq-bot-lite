"""LM Studio / OpenAI 兼容客户端：单次请求，可选流式。"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Callable

import httpx

from ..config import LLMConfig

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class ThinkingBudgetExceeded(RuntimeError):
    """思考量超过预算，主动掐断，交给上层用无思考模式重答。"""

    def __init__(self, tokens: int, budget: int):
        self.tokens = tokens
        self.budget = budget
        super().__init__(f"思考 {tokens} tokens 超过预算 {budget}")


class LLMClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.last_usage: dict[str, Any] | None = None
        self.last_finish_reason: str | None = None
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url.rstrip("/"),
            timeout=httpx.Timeout(connect=10.0, read=cfg.timeout, write=30.0, pool=10.0),
            trust_env=False,  # 本机模型，绝不走系统代理
            headers={"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _payload(
        self,
        messages: list[dict[str, Any]],
        stream: bool,
        override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "top_p": self.cfg.top_p,
            "max_tokens": self.cfg.max_tokens,
            "presence_penalty": self.cfg.presence_penalty,
            "frequency_penalty": self.cfg.frequency_penalty,
            "stream": stream,
        }
        if self.cfg.extra_body:
            payload.update(self.cfg.extra_body)
        if self.cfg.reasoning_effort:
            payload["reasoning_effort"] = self.cfg.reasoning_effort
        if override:
            payload.update(override)
        if stream:
            # LM Studio 默认流式不返回 usage，开了才能看到 reasoning token 数。
            payload.setdefault("stream_options", {"include_usage": True})
        return payload

    async def complete(self, messages: list[dict[str, Any]], override: dict[str, Any] | None = None) -> str:
        payload = self._payload(messages, stream=False, override=override)
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"请求模型失败: {exc}") from exc
        if resp.status_code >= 400:
            raise LLMError(_format_http_error(resp))
        data = resp.json()
        self.last_usage = data.get("usage")
        try:
            self.last_finish_reason = data["choices"][0].get("finish_reason")
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError) as exc:
            raise LLMError(f"模型返回结构异常: {json.dumps(data, ensure_ascii=False)[:500]}") from exc

    async def stream(
        self,
        messages: list[dict[str, Any]],
        override: dict[str, Any] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> AsyncIterator[str]:
        payload = self._payload(messages, stream=True, override=override)
        self.last_finish_reason = None
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise LLMError(f"模型返回 {resp.status_code}: {body[:500]}")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data.get("usage"), dict):
                        self.last_usage = data["usage"]
                    for choice in data.get("choices") or []:
                        if choice.get("finish_reason"):
                            self.last_finish_reason = choice["finish_reason"]
                        delta = choice.get("delta") or {}
                        think = delta.get("reasoning_content")
                        if think and on_reasoning is not None:
                            on_reasoning(str(think))
                        content = delta.get("content")
                        if content:
                            yield str(content)
        except httpx.HTTPError as exc:
            raise LLMError(f"流式请求模型失败: {exc}") from exc

    async def list_models(self) -> list[str]:
        resp = await self._client.get("/models")
        resp.raise_for_status()
        return [str(item.get("id")) for item in resp.json().get("data", [])]


def _format_http_error(resp: httpx.Response) -> str:
    text = resp.text[:800]
    return f"模型返回 {resp.status_code}: {text}"
