r"""端到端自测：假 SnowLuma + 假 LM Studio，不依赖 QQ 和真实模型。

跑法：.venv\Scripts\python.exe -m pytest tests -q
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from websockets.asyncio.client import connect

from app.config import Config
from app.main import build_app

TOKEN = "test-token"
SELF_ID = 10000
GROUP_ID = 777888
USER_ID = 123456

REPLY_TEXT = "在的在的。刚才在发呆，没看见你说话。有事就说，别磨叽。"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FakeState:
    def __init__(self) -> None:
        self.requests: list[dict] = []


def make_fake_llm(state: FakeState) -> FastAPI:
    app = FastAPI()

    @app.get("/v1/models")
    async def models() -> dict:
        return {"data": [{"id": "fake-model"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        state.requests.append(body)
        # 每次都换一句话，避免被“30 秒内相同内容去重”拦掉，让测试聚焦在触发逻辑上。
        text = REPLY_TEXT if len(state.requests) == 1 else f"第{len(state.requests)}次回复。" + REPLY_TEXT
        if not body.get("stream"):
            return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}

        async def gen():
            for index in range(0, len(text), 3):
                chunk = text[index : index + 3]
                payload = {"choices": [{"delta": {"content": chunk}}]}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.005)
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


async def start_server(app: FastAPI, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            raise RuntimeError("服务启动失败")
        await asyncio.sleep(0.02)
    return server


def group_event(message_id: int, text: str) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "self_id": SELF_ID,
        "user_id": USER_ID,
        "group_id": GROUP_ID,
        "message_id": message_id,
        "message": [
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "text", "data": {"text": text}},
        ],
        "sender": {"user_id": USER_ID, "nickname": "阿伟", "card": "阿伟"},
        "time": int(time.time()),
    }


async def collect_actions(ws, timeout: float = 3.0) -> list[dict]:
    """收服务端发来的 action，按 OneBot 规范回执；只收集发送类动作。"""
    actions: list[dict] = []
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return actions
        payload = json.loads(raw)
        if "action" not in payload:
            continue

        action = payload["action"]
        if action == "get_group_info":
            data = {"group_id": payload["params"].get("group_id"), "group_name": "测试群"}
        elif action.startswith("send_"):
            actions.append(payload)
            data = {"message_id": 5000 + len(actions)}
        else:
            data = {}
        await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": payload.get("echo")}))


@pytest.mark.asyncio
async def test_quote_reply_without_at_triggers(tmp_path: Path) -> None:
    """引用机器人的消息说话，不 @ 也算在跟它说话。"""
    state = FakeState()
    llm_port, app_port = free_port(), free_port()
    cfg = Config()
    cfg.server.port = app_port
    cfg.llm.base_url = f"http://127.0.0.1:{llm_port}/v1"
    cfg.llm.model = "fake-model"
    cfg.onebot.access_token = TOKEN
    cfg.reply.segment_interval = 0.0

    llm_server = await start_server(make_fake_llm(state), llm_port)
    bot_server = await start_server(build_app(cfg, db_path=tmp_path / "quote.db"), app_port)
    try:
        async with connect(
            f"ws://127.0.0.1:{app_port}/onebot/v11/ws",
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        ) as ws:
            # 第一轮：正常 @，拿到机器人回复的 message_id（假服务器回 5001）
            await ws.send(json.dumps(group_event(2001, "第一句")))
            await collect_actions(ws)

            # 第二轮：不 @，只引用刚才那条回复
            event = group_event(2002, "你什么意思")
            event["message"] = [
                {"type": "reply", "data": {"id": "5001"}},
                {"type": "text", "data": {"text": "你什么意思"}},
            ]
            await ws.send(json.dumps(event))
            actions = await collect_actions(ws)
    finally:
        bot_server.should_exit = True
        llm_server.should_exit = True
        await asyncio.sleep(0.2)

    assert actions, "引用机器人的消息应该触发回复"
    assert len(state.requests) == 2
    sent = json.dumps(state.requests[1]["messages"], ensure_ascii=False)
    assert "引用了你之前说的话" in sent


@pytest.mark.asyncio
async def test_bare_at_without_text_still_replies(tmp_path: Path) -> None:
    """只 @ 不说话也算点名，这是群里最常见的一种“叫一下”。"""
    state = FakeState()
    llm_port, app_port = free_port(), free_port()
    cfg = Config()
    cfg.server.port = app_port
    cfg.llm.base_url = f"http://127.0.0.1:{llm_port}/v1"
    cfg.llm.model = "fake-model"
    cfg.onebot.access_token = TOKEN
    cfg.reply.segment_interval = 0.0

    llm_server = await start_server(make_fake_llm(state), llm_port)
    bot_server = await start_server(build_app(cfg, db_path=tmp_path / "bare.db"), app_port)
    try:
        async with connect(
            f"ws://127.0.0.1:{app_port}/onebot/v11/ws",
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        ) as ws:
            event = group_event(1500, "")
            event["message"] = [{"type": "at", "data": {"qq": str(SELF_ID)}}]
            await ws.send(json.dumps(event))
            actions = await collect_actions(ws)
    finally:
        bot_server.should_exit = True
        llm_server.should_exit = True
        await asyncio.sleep(0.2)

    assert actions, "只 @ 一下也应该回"
    assert len(state.requests) == 1
    sent_text = json.dumps(state.requests[0]["messages"], ensure_ascii=False)
    assert "@了你一下" in sent_text, "空 @ 应该给模型一句可读的占位文本"


@pytest.mark.asyncio
async def test_group_reply_has_quote_at_and_segments(tmp_path: Path) -> None:
    state = FakeState()
    llm_port = free_port()
    app_port = free_port()

    cfg = Config()
    cfg.server.port = app_port
    cfg.llm.base_url = f"http://127.0.0.1:{llm_port}/v1"
    cfg.llm.model = "fake-model"
    cfg.llm.stream = True
    cfg.onebot.access_token = TOKEN
    cfg.reply.segment_interval = 0.0
    cfg.reply.max_segments = 3
    cfg.trigger.cooldown = 0.0

    llm_server = await start_server(make_fake_llm(state), llm_port)
    bot_server = await start_server(build_app(cfg, db_path=tmp_path / "test.db"), app_port)

    try:
        async with connect(
            f"ws://127.0.0.1:{app_port}/onebot/v11/ws",
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        ) as ws:
            await ws.send(json.dumps(group_event(901, "在吗")))
            actions = await collect_actions(ws)
    finally:
        bot_server.should_exit = True
        llm_server.should_exit = True
        await asyncio.sleep(0.2)

    assert actions, "没有收到任何发送动作"
    first = actions[0]
    assert first["action"] == "send_group_msg"
    assert first["params"]["group_id"] == GROUP_ID

    segments = first["params"]["message"]
    assert segments[0]["type"] == "reply" and segments[0]["data"]["id"] == "901"
    assert segments[1]["type"] == "at" and segments[1]["data"]["qq"] == str(USER_ID)

    joined = "".join(seg["data"].get("text", "") for action in actions for seg in action["params"]["message"])
    assert "发呆" in joined
    assert len(actions) >= 2, "长回复应该被拆成多条"

    # 只发了一次模型请求（没有 tool loop）
    assert len(state.requests) == 1
    body = state.requests[0]
    assert body["messages"][0]["role"] == "system"
    assert sum(1 for m in body["messages"] if m["role"] == "system") == 1
    assert body["messages"][-1]["role"] == "user"
    assert "在吗" in json.dumps(body["messages"][-1], ensure_ascii=False)


@pytest.mark.asyncio
async def test_history_accumulates_and_system_stays_at_front(tmp_path: Path) -> None:
    state = FakeState()
    llm_port = free_port()
    app_port = free_port()

    cfg = Config()
    cfg.server.port = app_port
    cfg.llm.base_url = f"http://127.0.0.1:{llm_port}/v1"
    cfg.llm.model = "fake-model"
    cfg.onebot.access_token = TOKEN
    cfg.reply.segment_interval = 0.0

    llm_server = await start_server(make_fake_llm(state), llm_port)
    bot_server = await start_server(build_app(cfg, db_path=tmp_path / "test2.db"), app_port)

    try:
        async with connect(
            f"ws://127.0.0.1:{app_port}/onebot/v11/ws",
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        ) as ws:
            await ws.send(json.dumps(group_event(1001, "第一句")))
            await collect_actions(ws)
            await ws.send(json.dumps(group_event(1002, "第二句")))
            await collect_actions(ws)
    finally:
        bot_server.should_exit = True
        llm_server.should_exit = True
        await asyncio.sleep(0.2)

    assert len(state.requests) == 2
    second = state.requests[1]["messages"]
    assert second[0]["role"] == "system"
    assert all(m["role"] != "system" for m in second[1:]), "system 只能出现在开头"
    dump = json.dumps(second, ensure_ascii=False)
    assert "第一句" in dump
    assert "发呆" in dump, "上一轮的机器人回复应进入上下文"


@pytest.mark.asyncio
async def test_wrong_token_is_rejected(tmp_path: Path) -> None:
    app_port = free_port()
    cfg = Config()
    cfg.server.port = app_port
    cfg.onebot.access_token = TOKEN
    bot_server = await start_server(build_app(cfg, db_path=tmp_path / "reject.db"), app_port)
    try:
        from websockets.exceptions import InvalidStatus, ConnectionClosed

        with pytest.raises((InvalidStatus, ConnectionClosed, OSError)):
            async with connect(f"ws://127.0.0.1:{app_port}/onebot/v11/ws") as ws:
                await ws.recv()
    finally:
        bot_server.should_exit = True
        await asyncio.sleep(0.2)
