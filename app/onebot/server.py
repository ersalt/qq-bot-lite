"""OneBot 11 反向 WebSocket 服务端：SnowLuma 主动连进来。"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
from typing import Any, Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from ..config import OneBotConfig
from .client import OneBotConnection
from .events import MessageEvent, parse_event

log = logging.getLogger(__name__)

MessageHandler = Callable[[MessageEvent], Awaitable[None]]


class OneBotServer:
    def __init__(self, cfg: OneBotConfig, handler: MessageHandler):
        self.cfg = cfg
        self.handler = handler
        self.connection: OneBotConnection | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self.self_id: int | None = None

    # ---------- 鉴权 ----------

    def _authorized(self, ws: WebSocket) -> bool:
        token = self.cfg.access_token.strip()
        if not token:
            return True
        header = ws.headers.get("authorization", "")
        supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not supplied:
            supplied = ws.query_params.get("access_token", "")
        return bool(supplied) and hmac.compare_digest(supplied, token)

    # ---------- 连接生命周期 ----------

    async def endpoint(self, ws: WebSocket) -> None:
        if not self._authorized(ws):
            log.warning("OneBot 连接鉴权失败，已拒绝: %s", ws.client)
            await ws.close(code=1008)
            return

        await ws.accept()
        conn = OneBotConnection(ws)

        if self.connection is not None and not self.connection.closed:
            if self.cfg.max_connections <= 1:
                log.warning("已有 OneBot 连接在线，按配置踢掉旧连接")
                await self.connection.close()
            else:
                log.warning("已有 OneBot 连接在线，但 max_connections>1，允许并存（可能重复回复）")

        self.connection = conn
        log.info("OneBot 已连接: %s", ws.client)

        try:
            while True:
                raw = await ws.receive_text()
                self._on_raw(conn, raw)
        except WebSocketDisconnect:
            log.info("OneBot 连接断开: %s", ws.client)
        except Exception:  # noqa: BLE001
            log.exception("OneBot 连接异常")
        finally:
            conn.closed = True
            if self.connection is conn:
                self.connection = None

    def _on_raw(self, conn: OneBotConnection, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("收到非 JSON 数据，已忽略: %.200s", raw)
            return
        if not isinstance(payload, dict):
            return

        if "post_type" in payload:
            conn.note_event(payload)
            if conn.self_id:
                self.self_id = conn.self_id
            event = parse_event(payload)
            if event is None:
                return
            task = asyncio.create_task(self._dispatch(event))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return

        if not conn.handle_response(payload):
            log.debug("未匹配的 action 回执: %.200s", raw)

    async def _dispatch(self, event: MessageEvent) -> None:
        try:
            await self.handler(event)
        except Exception:  # noqa: BLE001 - 单条消息失败不能拖垮连接
            log.exception("处理消息失败 session=%s", event.session_key)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self.connection is not None:
            await self.connection.close()
            self.connection = None
