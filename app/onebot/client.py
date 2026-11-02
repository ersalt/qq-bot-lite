"""OneBot 11 连接：发送 action 并按 echo 关联回执。"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)


class OneBotActionError(RuntimeError):
    def __init__(self, action: str, payload: dict[str, Any]):
        self.action = action
        self.payload = payload
        status = payload.get("status")
        retcode = payload.get("retcode")
        message = payload.get("message") or payload.get("wording") or ""
        super().__init__(f"{action} 失败: status={status} retcode={retcode} {message}".strip())


class OneBotConnection:
    """一个 SnowLuma 侧的 WS 连接。"""

    def __init__(self, websocket: WebSocket, label: str = "snowluma"):
        self.ws = websocket
        self.label = label
        self.self_id: int | None = None
        self._echo = itertools.count(1)
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._send_lock = asyncio.Lock()
        self.closed = False

    def note_event(self, payload: dict[str, Any]) -> None:
        self_id = payload.get("self_id")
        if isinstance(self_id, int) and self_id != self.self_id:
            self.self_id = self_id
            log.info("OneBot 账号 self_id=%s", self_id)

    async def call_action(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: float = 15.0,
    ) -> Any:
        if self.closed:
            raise OneBotActionError(action, {"status": "failed", "message": "连接已断开"})

        echo = str(next(self._echo))
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        payload = {"action": action, "params": params or {}, "echo": echo}
        try:
            async with self._send_lock:
                await self.ws.send_text(json.dumps(payload, ensure_ascii=False))
            response = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise OneBotActionError(action, {"status": "failed", "message": f"等待回执超时 {timeout}s"}) from None
        finally:
            self._pending.pop(echo, None)

        if response.get("status") != "ok" and response.get("retcode") not in (0, None):
            raise OneBotActionError(action, response)
        return response.get("data")

    def handle_response(self, payload: dict[str, Any]) -> bool:
        echo = payload.get("echo")
        if echo is None:
            return False
        future = self._pending.get(str(echo))
        if future is None or future.done():
            return False
        future.set_result(payload)
        return True

    async def send_group_msg(self, group_id: int, message: list[dict[str, Any]], timeout: float = 15.0) -> Any:
        return await self.call_action("send_group_msg", {"group_id": group_id, "message": message}, timeout)

    async def send_private_msg(self, user_id: int, message: list[dict[str, Any]], timeout: float = 15.0) -> Any:
        return await self.call_action("send_private_msg", {"user_id": user_id, "message": message}, timeout)

    async def get_group_info(self, group_id: int, timeout: float = 5.0) -> dict[str, Any]:
        data = await self.call_action("get_group_info", {"group_id": group_id}, timeout)
        return data if isinstance(data, dict) else {}

    async def close(self) -> None:
        self.closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(OneBotActionError("call", {"status": "failed", "message": "连接关闭"}))
        self._pending.clear()
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001 - 关闭阶段的异常无需上抛
            pass
