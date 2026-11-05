"""原生工具协议：client / 注册表 / ToolRunner / 管道接入。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.config import Config, LLMConfig
from app.core.pipeline import ChatService
from app.core.tool_runner import TOOL_CONTEXT_NOTE, ToolRunner
from app.llm.client import LLMClient
from app.pluginkit.manifest import PluginContext, read_manifest
from app.pluginkit.registry import Extension, ExtensionRegistry, HookContext
from app.pluginkit.tools import Tool, ToolContext, ToolEntry
from app.sdk import API_VERSION


def _entry(tmp_path: Path, handler, name: str = "echo") -> ToolEntry:
    tool = Tool(
        name=name,
        description="测试工具",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=handler,
    )
    ctx = PluginContext(name="demo", data_dir=tmp_path, log=None)
    return ToolEntry(tool=tool, plugin="demo", plugin_ctx=ctx)


def _context() -> ToolContext:
    return ToolContext(
        plugin="",
        session_key="private:1",
        is_group=False,
        is_admin=True,
        trigger="chat",
    )


class FakeLLM:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list[dict], list[dict] | None, dict | None]] = []

    async def complete_message(self, messages, tools=None, override=None):
        self.calls.append(([dict(item) for item in messages], tools, override))
        return self.responses.pop(0)


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


@pytest.mark.asyncio
async def test_complete_message_keeps_tool_calls() -> None:
    seen: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [_tool_call("space_append", {"path": "a.md"})],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            },
        )

    client = LLMClient(LLMConfig(base_url="http://test/v1", model="test", api_key=""))
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="http://test/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        message = await client.complete_message(
            [{"role": "user", "content": "记一下"}],
            tools=[{"type": "function", "function": {"name": "space_append", "parameters": {}}}],
        )
        assert message["tool_calls"][0]["function"]["name"] == "space_append"
        assert client.last_finish_reason == "tool_calls"
        assert seen[0]["tools"][0]["function"]["name"] == "space_append"
        assert seen[0]["tool_choice"] == "auto"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_registry_collects_tools_and_filters_duplicate(tmp_path: Path) -> None:
    async def echo(ctx, args):
        return args

    class First(Extension):
        name = "first"

        def tools(self):
            return [
                Tool(
                    name="demo_echo",
                    description="echo",
                    parameters={"type": "object"},
                    handler=echo,
                )
            ]

    class Second(Extension):
        name = "second"

        def tools(self):
            return [
                Tool(
                    name="demo_echo",
                    description="another",
                    parameters={"type": "object"},
                    handler=echo,
                )
            ]

    first = First()
    first.ctx = PluginContext(name="first", data_dir=tmp_path)
    second = Second()
    second.ctx = PluginContext(name="second", data_dir=tmp_path)
    registry = ExtensionRegistry([])
    registry.extensions = [first, second]
    entries = registry.collect_tools()

    assert list(entries) == ["demo_echo"]
    assert entries["demo_echo"].plugin == "first"


@pytest.mark.asyncio
async def test_tool_runner_executes_and_does_not_mutate_messages(tmp_path: Path) -> None:
    seen: list[dict] = []

    async def echo(ctx, args):
        seen.append(args)
        return {"echo": args["text"]}

    entry = _entry(tmp_path, echo)
    harness = FakeLLM(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tool_call("echo", {"text": "hi"})],
            },
            {"role": "assistant", "content": "done"},
        ]
    )
    messages = [{"role": "user", "content": "hi"}]
    result = await ToolRunner(harness).run(messages, {"echo": entry}, _context())

    assert result.text == "done"
    assert result.calls == 1 and result.rounds == 2
    assert seen == [{"text": "hi"}]
    assert messages == [{"role": "user", "content": "hi"}]
    second = harness.calls[1][0]
    assert [item["role"] for item in second] == ["user", "assistant", "tool"]
    assert second[-1]["tool_call_id"] == "call_1"
    payload = json.loads(second[-1]["content"])
    assert payload["ok"] is True
    assert payload["result"] == {"echo": "hi"}
    assert payload["context_note"] == TOOL_CONTEXT_NOTE


@pytest.mark.asyncio
async def test_tool_runner_returns_tool_errors_to_model(tmp_path: Path) -> None:
    async def broken(ctx, args):
        raise ValueError("坏了")

    entry = _entry(tmp_path, broken, name="broken")
    harness = FakeLLM(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tool_call("broken", {})],
            },
            {"role": "assistant", "content": "我换个办法"},
        ]
    )
    result = await ToolRunner(harness).run([{"role": "user", "content": "试"}], {"broken": entry}, _context())

    assert result.text == "我换个办法"
    tool_message = harness.calls[1][0][-1]
    payload = json.loads(tool_message["content"])
    assert payload["ok"] is False
    assert "ValueError" in payload["error"]


@pytest.mark.asyncio
async def test_tool_runner_limits_rounds_and_truncates_result(tmp_path: Path) -> None:
    async def huge(ctx, args):
        return "x" * 2000

    entry = _entry(tmp_path, huge, name="huge")
    always = {
        "role": "assistant",
        "content": "",
        "tool_calls": [_tool_call("huge", {})],
    }
    harness = FakeLLM([always, always, always, always, always])
    runner = ToolRunner(harness, max_rounds=2, max_result_chars=120)
    result = await runner.run([{"role": "user", "content": "loop"}], {"huge": entry}, _context())

    assert result.text == ""
    assert result.rounds == 3
    assert result.calls == 2
    tool_message = harness.calls[1][0][-1]
    payload = json.loads(tool_message["content"])
    assert payload["truncated"] is True
    assert len(tool_message["content"]) <= 120


@pytest.mark.asyncio
async def test_tool_runner_turns_unknown_and_timeout_into_tool_errors(tmp_path: Path) -> None:
    async def slow(ctx, args):
        await asyncio.sleep(0.2)
        return "太晚了"

    entry = _entry(tmp_path, slow, name="slow")
    harness = FakeLLM(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    _tool_call("missing", {}, call_id="call_missing"),
                    _tool_call("slow", {}, call_id="call_slow"),
                ],
            },
            {"role": "assistant", "content": "都失败了"},
        ]
    )
    result = await ToolRunner(harness, tool_timeout=0.01).run(
        [{"role": "user", "content": "试"}],
        {"slow": entry},
        _context(),
    )

    assert result.text == "都失败了"
    tool_messages = [item for item in harness.calls[1][0] if item["role"] == "tool"]
    assert len(tool_messages) == 2
    assert json.loads(tool_messages[0]["content"])["ok"] is False
    assert "未知工具" in json.loads(tool_messages[0]["content"])["error"]
    assert "超时" in json.loads(tool_messages[1]["content"])["error"]


@pytest.mark.asyncio
async def test_chat_pieces_uses_tools_and_keeps_history_clean(tmp_path: Path) -> None:
    called: list[dict] = []

    async def echo(ctx, args):
        called.append(args)
        return "记好了"

    entry = _entry(tmp_path, echo)
    harness = FakeLLM(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tool_call("echo", {"text": "hi"})],
            },
            {"role": "assistant", "content": "记好了"},
        ]
    )
    registry = ExtensionRegistry([])
    extension = Extension()
    extension.ctx = PluginContext(name="demo", data_dir=tmp_path)
    registry.extensions = [extension]

    service = object.__new__(ChatService)
    service.cfg = Config()
    service.cfg.reply.segments = False
    service.extensions = registry
    service.tool_runner = ToolRunner(harness)

    messages = [{"role": "user", "content": "记住"}]
    collected: list[str] = []
    hook = HookContext(event=SimpleNamespace(), session_key="private:1", is_group=False)
    pieces = [
        piece
        async for piece in service._pieces(
            messages,
            hook,
            collected,
            _timing(),
            tools={"echo": entry},
            tool_context=_context(),
        )
    ]

    assert pieces == ["记好了"]
    assert collected == ["记好了"]
    assert called == [{"text": "hi"}]
    assert messages == [{"role": "user", "content": "记住"}]
    assert harness.calls[0][1]


def test_tools_available_in_all_sessions(tmp_path: Path) -> None:
    async def echo(ctx, args):
        return args

    registry = ExtensionRegistry([])
    extension = Extension()
    extension.ctx = PluginContext(name="demo", data_dir=tmp_path)
    registry.extensions = [extension]
    registry.collect_tools = lambda: {"echo": _entry(tmp_path, echo)}  # type: ignore[method-assign]

    service = object.__new__(ChatService)
    service.extensions = registry
    assert "echo" in service._tools_for(SimpleNamespace(is_group=False))
    assert "echo" in service._tools_for(SimpleNamespace(is_group=True))


def test_api_version_two_and_old_plugins_still_load(tmp_path: Path) -> None:
    assert API_VERSION == 2
    root = tmp_path / "plugins"
    plugin = root / "old"
    plugin.mkdir(parents=True)
    (plugin / "plugin.toml").write_text('title = "旧插件"\napi = 1\n', encoding="utf-8")
    (plugin / "plugin.py").write_text(
        "def setup(ctx):\n    return None\n",
        encoding="utf-8",
    )
    manifest = read_manifest(root, "old")
    assert manifest.error is None
    assert manifest.api == 1


def _timing():
    from app.core.pipeline import _Timing

    return _Timing()
