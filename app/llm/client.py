"""LM Studio / OpenAI 兼容客户端：单次请求，可选流式。"""

from __future__ import annotations

import json
import logging
import ssl
from typing import Any, AsyncIterator, Callable

import httpx

from ..config import LLMConfig

log = logging.getLogger(__name__)

# Windows 上 ssl.create_default_context 要扫一遍证书库，实测 600~800ms。
# httpx 每次建客户端都会造一个，切个模型配置就卡一秒。这里只造一次，之后复用。
_SSL_CONTEXT: ssl.SSLContext | None = None


def _ssl_context() -> ssl.SSLContext:
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        try:
            import certifi

            _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
        except Exception:  # noqa: BLE001 - 没有 certifi 就用系统证书库
            _SSL_CONTEXT = ssl.create_default_context()
    return _SSL_CONTEXT


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
        self._transport = self._fingerprint()
        self._client = self._build()

    def _fingerprint(self) -> tuple[str, str]:
        return (self.cfg.base_url.rstrip("/"), self.cfg.api_key or "")

    def _build(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._fingerprint()[0],
            timeout=httpx.Timeout(connect=10.0, read=self.cfg.timeout, write=30.0, pool=10.0),
            trust_env=False,  # 本机模型，绝不走系统代理
            verify=_ssl_context(),
            headers={"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def reload(self) -> None:
        """切换模型配置后重建连接池。

        base_url / api_key 没变就不重建：建一个 httpx 客户端要重新摸 SSL 上下文，
        Windows 上要 600ms 以上，切个配置卡一秒不值得。
        """
        fingerprint = self._fingerprint()
        if fingerprint == self._transport:
            return
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001 - 关不掉也要继续
            log.debug("关闭旧连接池失败", exc_info=True)
        self._transport = fingerprint
        self._client = self._build()

    def _payload(
        self,
        messages: list[dict[str, Any]],
        stream: bool,
        override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": stream,
        }
        # 采样参数留空就不发，交给 LM Studio 里模型自己的设置。
        if self.cfg.temperature is not None:
            payload["temperature"] = self.cfg.temperature
        if self.cfg.top_p is not None:
            payload["top_p"] = self.cfg.top_p
        # 惩罚项是 0 就不发，省得请求体里堆一串没意义的 0。
        if self.cfg.presence_penalty:
            payload["presence_penalty"] = self.cfg.presence_penalty
        if self.cfg.frequency_penalty:
            payload["frequency_penalty"] = self.cfg.frequency_penalty
        # 这两个留空就不发，跟 AstrBot 一样。抓过它的真包：它只发 model/stream/messages/
        # tool_choice/tools，没有 max_tokens、没有 reasoning_effort —— 推理长度完全交给
        # 模型自己按“允许说多长”决定，实测反而更快更稳。
        if self.cfg.max_tokens > 0:
            payload["max_tokens"] = self.cfg.max_tokens
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

    async def complete_message(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        override: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """一次非流式请求，返回完整的 assistant message。

        和 complete() 的区别：这里保留 tool_calls / reasoning_content，工具循环需要完整
        消息；普通聊天仍走 complete()，行为不变。
        """
        payload = self._payload(messages, stream=False, override=override)
        # complete_message 的语义就是非流式；override 里就算写了 stream 也不放行。
        payload["stream"] = False
        if tools:
            payload["tools"] = tools
            payload.setdefault("tool_choice", "auto")
        kwargs: dict[str, Any] = {}
        if timeout:
            kwargs["timeout"] = httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=10.0)
        try:
            resp = await self._client.post("/chat/completions", json=payload, **kwargs)
        except httpx.HTTPError as exc:
            raise LLMError(f"请求模型失败: {exc}") from exc
        if resp.status_code >= 400:
            raise LLMError(_format_http_error(resp))
        data = resp.json()
        self.last_usage = data.get("usage")
        try:
            choice = data["choices"][0]
            self.last_finish_reason = choice.get("finish_reason")
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"模型返回结构异常: {json.dumps(data, ensure_ascii=False)[:500]}") from exc
        if not isinstance(message, dict):
            raise LLMError("模型返回的 message 不是对象")
        return message

    async def complete(
        self,
        messages: list[dict[str, Any]],
        override: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> str:
        """一次非流式请求，只要正文（保持原有调用方不变）。"""
        message = await self.complete_message(messages, override=override, timeout=timeout)
        return str(message.get("content") or "")

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
