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
        items = store.list_sessions()
        # 旧会话没有存过群名；连着 OneBot 时顺手补一次，私聊用最后说话人的昵称。
        server = getattr(app.state, "server", None)
        conn = getattr(server, "connection", None)
        if conn is not None:
            for item in items:
                if item["kind"] != "group" or item["name"]:
                    continue
                try:
                    info = await conn.get_group_info(item["target_id"], timeout=1.2)
                    name = str(info.get("group_name") or "").strip()
                except Exception:  # noqa: BLE001 - 补名字失败不影响列表
                    name = ""
                if name:
                    store.note_session_name(item["key"], name)
                    item["name"] = name
        return JSONResponse({"ok": True, "items": items, "stats": store.stats()})

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
