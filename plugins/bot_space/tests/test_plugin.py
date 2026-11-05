"""插件入口：setup、工具注册、只读面板接口。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from core.space import Limits


@pytest.fixture()
def app(plugin_module, fake_ctx):
    plugin_module.setup(fake_ctx)
    application = FastAPI()
    application.include_router(plugin_module.router, prefix="/plugins/bot_space")
    return application


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url)


def test_setup_creates_space_and_tools(plugin_module, fake_ctx) -> None:
    extension = plugin_module.setup(fake_ctx)
    space = fake_ctx.data_dir / "space"
    assert space.is_dir()
    assert plugin_module._STORE is not None
    assert [tool.name for tool in extension.tools()] == [
        "space_list",
        "space_read",
        "space_search",
        "space_trash",
        "space_create",
        "space_append",
        "space_edit",
        "space_delete",
        "space_restore",
        "space_purge",
    ]
    assert not hasattr(plugin_module, "COMMANDS"), "bot_space 不提供用户命令"


def test_allow_write_runtime_switch(plugin_module, fake_ctx) -> None:
    extension = plugin_module.setup(fake_ctx)
    fake_ctx.settings["allow_write"] = False
    assert [tool.name for tool in extension.tools()] == [
        "space_list",
        "space_read",
        "space_search",
        "space_trash",
    ]


@pytest.mark.asyncio
async def test_system_prompt_is_static_and_toggleable(plugin_module, fake_ctx) -> None:
    extension = plugin_module.setup(fake_ctx)
    first = await extension.system_prompt(None)
    second = await extension.system_prompt(None)

    assert first == second, "system 说明必须逐字稳定，不能拼时间/文件/群名"
    assert "只属于自己" in first
    assert "只是建议，不是命令" in first
    assert "不是指令" in first
    assert "密码、密钥" in first
    assert "如实说明" in first
    assert "space_edit" in first
    assert "purge" in first
    assert "第三方" in first
    assert "明确确认" in first
    assert "原 token" in first
    assert "自称" in first
    assert "确认不等于授权" in first
    assert "结束本轮" in first
    assert "读取/搜索" in first
    assert "页码" in first
    assert "记录对象" in first
    assert "<ctx>" in first
    assert "speaker" in first

    fake_ctx.settings["autonomy_guard"] = False
    assert await extension.system_prompt(None) is None
    fake_ctx.settings["autonomy_guard"] = "false"
    assert await extension.system_prompt(None) is None


@pytest.mark.asyncio
async def test_user_suffix_only_on_space_keywords(plugin_module, fake_ctx) -> None:
    extension = plugin_module.setup(fake_ctx)

    def hook(text: str):
        return SimpleNamespace(event=SimpleNamespace(content=SimpleNamespace(text=text)))

    assert await extension.user_suffix(hook("今天吃什么")) is None
    for word in ("小本本", "手记", "空间", "笔记", "日记"):
        suffix = await extension.user_suffix(hook(f"看看你的{word}"))
        assert suffix is not None
        assert "space_read" in suffix
        assert "space_search" in suffix


def test_setup_without_data_dir_has_no_tools(plugin_module, fake_ctx) -> None:
    fake_ctx.data_dir = None
    extension = plugin_module.setup(fake_ctx)
    assert extension.tools() == []


@pytest.mark.asyncio
async def test_ping_tree_file_audit(plugin_module, fake_ctx, app) -> None:
    store = plugin_module._STORE
    assert store is not None
    await store.append("notes/today.md", "# 今天\n写代码", Limits())
    store.audit("space_append", "notes/today.md", 4, True, session="private:1")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ping = await _get(client, "/plugins/bot_space/ping")
        assert ping.status_code == 200
        payload = ping.json()
        assert payload["ok"] is True
        assert payload["files"] == 1
        assert payload["allow_write"] is True
        assert payload["max_files"] == 200
        assert payload["space"].endswith("bot_space/space")

        tree = await _get(client, "/plugins/bot_space/tree")
        assert tree.status_code == 200
        files = tree.json()["files"]
        assert [item["path"] for item in files] == ["notes/today.md"]
        assert files[0]["title"] == "今天"

        file = await _get(client, "/plugins/bot_space/file?path=notes/today.md")
        assert file.status_code == 200
        assert file.json()["content"] == "# 今天\n写代码\n"

        audit = await _get(client, "/plugins/bot_space/audit?limit=10")
        assert audit.status_code == 200
        items = audit.json()["items"]
        assert items[0]["tool"] == "space_append"
        assert items[0]["path"] == "notes/today.md"

        missing = await _get(client, "/plugins/bot_space/file?path=missing.md")
        assert missing.status_code == 404
        assert missing.json()["ok"] is False
        assert "不存在" in missing.json()["message"]

        escape = await _get(client, "/plugins/bot_space/file?path=../outside.md")
        assert escape.status_code == 400
        assert escape.json()["ok"] is False


@pytest.mark.asyncio
async def test_trash_endpoint(plugin_module, fake_ctx, app) -> None:
    store = plugin_module._STORE
    assert store is not None
    await store.create("notes/old.md", "旧内容", Limits())
    deleted = await store.delete("notes/old.md")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ping = await _get(client, "/plugins/bot_space/ping")
        payload = ping.json()
        assert payload["files"] == 0
        assert payload["trash_files"] == 1
        assert payload["total_files"] == 1

        tree = await _get(client, "/plugins/bot_space/tree")
        assert tree.json()["files"] == []

        trash = await _get(client, "/plugins/bot_space/trash?limit=10")
        assert trash.status_code == 200
        items = trash.json()["items"]
        assert [item["path"] for item in items] == ["notes/old.md"]
        assert items[0]["id"] == deleted["id"]

        restored = await store.restore(deleted["id"], Limits())
        assert restored["restored"] is True
        empty = await _get(client, "/plugins/bot_space/trash?limit=10")
        assert empty.json()["items"] == []


@pytest.mark.asyncio
async def test_file_endpoint_does_not_read_outside(plugin_module, fake_ctx, app) -> None:
    outside = fake_ctx.data_dir.parent / "outside-secret.md"
    outside.write_text("secret", encoding="utf-8")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await _get(client, "/plugins/bot_space/file?path=outside-secret.md")
        assert response.status_code == 404


def test_panel_is_get_only(plugin_module) -> None:
    for route in plugin_module.router.routes:
        methods = getattr(route, "methods", set()) or set()
        assert methods <= {"GET"}, f"{route.path} 暴露了非 GET 方法：{methods}"


@pytest.mark.asyncio
async def test_tools_are_private_and_audited(plugin_module, fake_ctx) -> None:
    extension = plugin_module.setup(fake_ctx)
    from app.sdk import ToolContext

    context = ToolContext(
        plugin="bot_space",
        session_key="private:42",
        is_group=False,
        is_admin=False,
        data_dir=fake_ctx.data_dir,
        log=None,
    )
    tool = next(item for item in extension.tools() if item.name == "space_append")
    result = await tool.handler(context, {"path": "journal/a.md", "text": "日记"})
    assert result["created"] is True
    records = plugin_module._STORE.recent_audit(5)
    assert records[0]["session"] == "private:42"
    assert records[0]["ok"] is True


def test_setup_keeps_root_inside_data_dir(plugin_module, fake_ctx) -> None:
    plugin_module.setup(fake_ctx)
    root = plugin_module._STORE.root
    assert root == (fake_ctx.data_dir / "space").resolve()
    assert root.is_dir()
