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
from fastapi.responses import Response, StreamingResponse
from websockets.asyncio.client import connect

from app.config import Config
from app.main import build_app

TOKEN = "test-token"
SELF_ID = 10000
GROUP_ID = 777888
USER_ID = 123456

REPLY_TEXT = "在的在的。刚才在发呆，没看见你说话。有事就说，别磨叽。"

# get_msg 假装被引用那条消息里有一张图；具体地址由用到它的用例改成能访问的本地地址。
QUOTED_IMAGE_URL = "https://example.invalid/cat.png"


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


def group_event(message_id: int, text: str) -> dict:    return {
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
        elif action == "get_group_member_list":
            data = [
                {"user_id": 999, "card": "饺子", "nickname": "饺子"},
                {"user_id": USER_ID, "card": "阿伟", "nickname": "阿伟"},
            ]
        elif action == "get_msg":
            asked = str(payload["params"].get("message_id"))
            # 只有指定那条“被引用的图”才带图，其余引用返回纯文字，
            # 免得每个引用用例都被塞进一张图。
            segments = (
                [{"type": "image", "data": {"url": QUOTED_IMAGE_URL, "file": "cat.png"}}]
                if asked == "4242"
                else [{"type": "text", "data": {"text": "之前说过的话"}}]
            )
            data = {"message_id": payload["params"].get("message_id"), "message": segments}
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
async def test_empty_reply_falls_back_without_thinking(tmp_path: Path) -> None:
    """思考把配额吃光、正文为空时，要自动换无思考模式重答一次。"""
    state = FakeState()
    llm_port, app_port = free_port(), free_port()

    app = FastAPI()

    @app.get("/v1/models")
    async def models() -> dict:
        return {"data": [{"id": "fake-model"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        state.requests.append(body)
        text = "" if len(state.requests) == 1 else "兜底回复来了。"
        if not body.get("stream"):
            return {"choices": [{"message": {"content": text}}], "usage": {"completion_tokens": 0}}

        async def gen():
            for index in range(0, len(text), 3):
                payload = {"choices": [{"delta": {"content": text[index : index + 3]}}]}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    cfg = Config()
    cfg.server.port = app_port
    cfg.llm.base_url = f"http://127.0.0.1:{llm_port}/v1"
    cfg.llm.model = "fake-model"
    cfg.onebot.access_token = TOKEN
    cfg.reply.segment_interval = 0.0

    llm_server = await start_server(app, llm_port)
    bot_server = await start_server(build_app(cfg, db_path=tmp_path / "fallback.db"), app_port)
    try:
        async with connect(
            f"ws://127.0.0.1:{app_port}/onebot/v11/ws",
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        ) as ws:
            await ws.send(json.dumps(group_event(3001, "说点什么")))
            actions = await collect_actions(ws)
    finally:
        bot_server.should_exit = True
        llm_server.should_exit = True
        await asyncio.sleep(0.2)

    assert len(state.requests) == 2, "空回复后应该重答一次"
    assert state.requests[1].get("reasoning_effort") == "none", "重答要关思考"
    assert actions, "重答的内容应该发出去"
    assert "兜底回复" in json.dumps(actions, ensure_ascii=False)


@pytest.mark.asyncio
async def test_mention_in_reply_becomes_real_at(tmp_path: Path) -> None:
    """模型写 @饺子 要变成真正的 at 段，而不是一段普通文字。"""
    state = FakeState()
    llm_port, app_port = free_port(), free_port()

    app = FastAPI()

    @app.get("/v1/models")
    async def models() -> dict:
        return {"data": [{"id": "fake-model"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        state.requests.append(body)
        text = "行，at 饺子 你过来看看。"
        if not body.get("stream"):
            return {"choices": [{"message": {"content": text}}]}

        async def gen():
            for index in range(0, len(text), 4):
                payload = {"choices": [{"delta": {"content": text[index : index + 4]}}]}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    cfg = Config()
    cfg.server.port = app_port
    cfg.llm.base_url = f"http://127.0.0.1:{llm_port}/v1"
    cfg.llm.model = "fake-model"
    cfg.onebot.access_token = TOKEN
    cfg.reply.segment_interval = 0.0

    llm_server = await start_server(app, llm_port)
    bot_server = await start_server(build_app(cfg, db_path=tmp_path / "mention.db"), app_port)
    try:
        async with connect(
            f"ws://127.0.0.1:{app_port}/onebot/v11/ws",
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        ) as ws:
            await ws.send(json.dumps(group_event(4001, "把饺子叫来")))
            actions = await collect_actions(ws)
    finally:
        bot_server.should_exit = True
        llm_server.should_exit = True
        await asyncio.sleep(0.2)

    assert actions, "应该有回复"
    segments = actions[0]["params"]["message"]
    ats = [seg for seg in segments if seg["type"] == "at"]
    assert ats and ats[0]["data"]["qq"] == "999", f"@饺子 没解析成 at 段: {segments}"
    joined = "".join(seg["data"].get("text", "") for seg in segments)
    assert "@饺子" not in joined, "解析成功后不该再把字面 @饺子 发出去"


@pytest.mark.asyncio
async def test_mention_with_explicit_qq_not_duplicated(tmp_path: Path) -> None:
    """模型照着历史写成 “at 饺子(999)” 时，不能把括号里的号再当正文发一遍。"""
    state = FakeState()
    llm_port, app_port = free_port(), free_port()

    app = FastAPI()

    @app.get("/v1/models")
    async def models() -> dict:
        return {"data": [{"id": "fake-model"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        state.requests.append(body)
        text = "行，at 饺子(999) 你过来看看。"
        if not body.get("stream"):
            return {"choices": [{"message": {"content": text}}]}

        async def gen():
            for index in range(0, len(text), 4):
                payload = {"choices": [{"delta": {"content": text[index : index + 4]}}]}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    cfg = Config()
    cfg.server.port = app_port
    cfg.llm.base_url = f"http://127.0.0.1:{llm_port}/v1"
    cfg.llm.model = "fake-model"
    cfg.onebot.access_token = TOKEN
    cfg.reply.segment_interval = 0.0

    llm_server = await start_server(app, llm_port)
    bot_server = await start_server(build_app(cfg, db_path=tmp_path / "mention-qq.db"), app_port)
    try:
        async with connect(
            f"ws://127.0.0.1:{app_port}/onebot/v11/ws",
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        ) as ws:
            await ws.send(json.dumps(group_event(4002, "把饺子叫来")))
            actions = await collect_actions(ws)
    finally:
        bot_server.should_exit = True
        llm_server.should_exit = True
        await asyncio.sleep(0.2)

    assert actions, "应该有回复"
    segments = actions[0]["params"]["message"]
    ats = [seg for seg in segments if seg["type"] == "at"]
    assert len(ats) == 1 and ats[0]["data"]["qq"] == "999", f"at 段不对: {segments}"
    joined = "".join(seg["data"].get("text", "") for seg in segments)
    assert "999" not in joined, f"QQ 号被当正文重复发了: {joined!r}"


@pytest.mark.asyncio
async def test_inbound_at_expands_to_name_and_id(tmp_path: Path) -> None:
    """收到 “@某人 叫这个 儿子” 时，模型必须看到 at 名字(QQ号)，不然不知道在叫谁。"""
    state = FakeState()
    llm_port, app_port = free_port(), free_port()
    cfg = Config()
    cfg.server.port = app_port
    cfg.llm.base_url = f"http://127.0.0.1:{llm_port}/v1"
    cfg.llm.model = "fake-model"
    cfg.onebot.access_token = TOKEN
    cfg.reply.segment_interval = 0.0

    llm_server = await start_server(make_fake_llm(state), llm_port)
    bot_server = await start_server(build_app(cfg, db_path=tmp_path / "inbound-at.db"), app_port)
    try:
        async with connect(
            f"ws://127.0.0.1:{app_port}/onebot/v11/ws",
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        ) as ws:
            event = group_event(6001, "")
            event["message"] = [
                {"type": "at", "data": {"qq": str(SELF_ID)}},
                {"type": "text", "data": {"text": " 叫这个 "}},
                {"type": "at", "data": {"qq": "999"}},
                {"type": "text", "data": {"text": " 儿子"}},
                {"type": "at", "data": {"qq": str(USER_ID)}},
            ]
            await ws.send(json.dumps(event))
            await collect_actions(ws)
    finally:
        bot_server.should_exit = True
        llm_server.should_exit = True
        await asyncio.sleep(0.2)

    sent = json.dumps(state.requests[0]["messages"], ensure_ascii=False)
    assert "at 饺子(999)" in sent, sent
    assert f"at 阿伟({USER_ID})" in sent, sent


@pytest.mark.asyncio
async def test_quoted_image_is_fetched_and_sent_to_model(tmp_path: Path) -> None:
    """引用一张图再说“识图”：要能用 get_msg 把那张图取过来送进模型。"""
    global QUOTED_IMAGE_URL
    state = FakeState()
    llm_port, app_port = free_port(), free_port()

    app = make_fake_llm(state)

    @app.get("/cat.png")
    async def cat() -> Response:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (32, 32), "red").save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")

    cfg = Config()
    cfg.server.port = app_port
    cfg.llm.base_url = f"http://127.0.0.1:{llm_port}/v1"
    cfg.llm.model = "fake-model"
    cfg.onebot.access_token = TOKEN
    cfg.reply.segment_interval = 0.0

    old_url = QUOTED_IMAGE_URL
    QUOTED_IMAGE_URL = f"http://127.0.0.1:{llm_port}/cat.png"
    llm_server = await start_server(app, llm_port)
    bot_server = await start_server(build_app(cfg, db_path=tmp_path / "quote-img.db"), app_port)
    try:
        async with connect(
            f"ws://127.0.0.1:{app_port}/onebot/v11/ws",
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        ) as ws:
            event = group_event(7001, " 识图")
            event["message"] = [
                {"type": "reply", "data": {"id": "4242"}},
                {"type": "at", "data": {"qq": str(SELF_ID)}},
                {"type": "text", "data": {"text": " 识图"}},
            ]
            await ws.send(json.dumps(event))
            await collect_actions(ws)
    finally:
        QUOTED_IMAGE_URL = old_url
        bot_server.should_exit = True
        llm_server.should_exit = True
        await asyncio.sleep(0.2)

    assert state.requests, "应该有模型请求"
    body = json.dumps(state.requests[0]["messages"], ensure_ascii=False)
    assert "image_url" in body, f"引用里的图没送进模型: {body[:400]}"


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
    cfg.reply.at_sender = True  # 默认关了，这条测试专门验证 @ 能力
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

