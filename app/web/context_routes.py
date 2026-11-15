"""上下文清理接口：列出本地会话，按会话或全部删除聊天记录。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..context.store import MessageStore

log = logging.getLogger(__name__)


def mount_context_api(app: FastAPI, store: MessageStore) -> None:
    @app.get("/api/context/sessions")
    async def list_sessions() -> JSONResponse:
        return JSONResponse({"ok": True, "items": store.list_sessions(), "stats": store.stats()})

    @app.post("/api/context/clear")
    async def clear_context(request: Request) -> JSONResponse:
        try:
            body: dict[str, Any] = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请求体不是 JSON") from exc

        if body.get("all") is True:
            counts = store.clear_all()
            log.info("清空全部聊天上下文: %s", counts)
            return JSONResponse(
                {"ok": True, "cleared": "all", "counts": counts, "items": store.list_sessions()}
            )

        session = str(body.get("session") or "").strip()
        if not session:
            return JSONResponse(
                {"ok": False, "message": "缺少要清理的会话"}, status_code=400
            )
        known = {item["key"] for item in store.list_sessions()}
        if session not in known:
            return JSONResponse(
                {"ok": False, "message": "会话不存在或已经被清理"}, status_code=404
            )

        counts = store.clear_session(session)
        log.info("清理会话上下文: %s %s", session, counts)
        return JSONResponse(
            {
                "ok": True,
                "cleared": session,
                "counts": counts,
                "items": store.list_sessions(),
            }
        )
