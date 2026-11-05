"""工具层：schema、只读/写权限、审计、配额错误。"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.sdk import ToolContext

from core.space import SpaceError, SpaceStore
from core.tools import build_tools


@pytest.fixture()
def store(tmp_path: Path) -> SpaceStore:
    instance = SpaceStore(tmp_path / "space")
    instance.ensure()
    return instance


def _ctx(session: str = "private:1", *, is_group: bool = False) -> ToolContext:
    return ToolContext(
        plugin="bot_space",
        session_key=session,
        is_group=is_group,
        is_admin=True,
        data_dir=None,
        log=None,
    )


def _tool(tools, name: str):
    return next(item for item in tools if item.name == name)


def _names(tools) -> list[str]:
    return [item.name for item in tools]


async def _confirmed(tool, ctx, args: dict) -> dict:
    first = await tool.handler(ctx, args)
    assert first["needs_confirmation"] is True
    assert first["confirm_token"]
    return await tool.handler(ctx, {**args, "confirm_token": first["confirm_token"]})


def test_read_tools_always_present(store: SpaceStore) -> None:
    tools = build_tools(store, lambda: {})
    assert _names(tools) == [
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


def test_allow_write_off_hides_write_tools(store: SpaceStore) -> None:
    settings = {"allow_write": False}
    tools = build_tools(store, lambda: settings)
    assert _names(tools) == ["space_list", "space_read", "space_search", "space_trash"]
    settings["allow_write"] = True
    assert "space_append" in _names(build_tools(store, lambda: settings))
    settings["allow_write"] = "false"
    assert "space_append" not in _names(build_tools(store, lambda: settings))


@pytest.mark.asyncio
async def test_tools_roundtrip(store: SpaceStore) -> None:
    tools = build_tools(store, lambda: {})
    ctx = _ctx()

    appended = await _tool(tools, "space_append").handler(
        ctx, {"path": "notes/today.md", "text": "# 今天\n写代码"}
    )
    assert appended["created"] is True
    assert appended["path"] == "notes/today.md"

    read = await _tool(tools, "space_read").handler(ctx, {"path": "notes/today.md"})
    assert read["content"] == "# 今天\n写代码\n"

    listing = await _tool(tools, "space_list").handler(ctx, {})
    assert listing["total"] == 1
    assert listing["files"][0]["title"] == "今天"

    found = await _tool(tools, "space_search").handler(ctx, {"query": "代码"})
    assert found["total"] == 1
    assert found["hits"][0]["line"] == 2

    created = await _tool(tools, "space_create").handler(
        ctx, {"path": "journal/day.md", "text": "# 日记\n"}
    )
    assert created["created"] is True

    records = store.recent_audit(10)
    assert {item["tool"] for item in records} == {
        "space_append",
        "space_read",
        "space_list",
        "space_search",
        "space_create",
    }
    assert all(item["ok"] for item in records)
    assert all(item["session"] == "private:1" for item in records)


@pytest.mark.asyncio
async def test_edit_delete_trash_restore_tools(store: SpaceStore) -> None:
    tools = build_tools(store, lambda: {})
    ctx = _ctx()
    await _tool(tools, "space_append").handler(
        ctx, {"path": "notes/list.md", "text": "1. 贰叔\n2. 饺子"}
    )

    edited = await _confirmed(
        _tool(tools, "space_edit"),
        ctx,
        {"path": "notes/list.md", "old": "2. 饺子\n", "new": ""},
    )
    assert edited["replaced"] == 1
    read = await _tool(tools, "space_read").handler(ctx, {"path": "notes/list.md"})
    assert read["content"] == "1. 贰叔\n"

    deleted = await _confirmed(
        _tool(tools, "space_delete"), ctx, {"path": "notes/list.md"}
    )
    assert deleted["restorable"] is True
    assert store.list_files()["total"] == 0

    trash = await _tool(tools, "space_trash").handler(ctx, {})
    assert trash["total"] == 1
    item = trash["items"][0]
    assert item["path"] == "notes/list.md"

    restored = await _confirmed(
        _tool(tools, "space_restore"), ctx, {"id": item["id"]}
    )
    assert restored["restored"] is True
    read = await _tool(tools, "space_read").handler(ctx, {"path": "notes/list.md"})
    assert read["content"] == "1. 贰叔\n"
    assert any(item["tool"] == "space_restore" for item in store.recent_audit(10))

    await _confirmed(
        _tool(tools, "space_delete"), ctx, {"path": "notes/list.md"}
    )
    trash = await _tool(tools, "space_trash").handler(ctx, {})
    purged = await _confirmed(
        _tool(tools, "space_purge"), ctx, {"id": trash["items"][0]["id"]}
    )
    assert purged["purged"] is True
    assert store.trash_items()["total"] == 0


@pytest.mark.asyncio
async def test_edit_requires_unique_match(store: SpaceStore) -> None:
    await store.create("a.md", "同一行\n同一行\n", store_limits())
    tools = build_tools(store, lambda: {})
    with pytest.raises(SpaceError, match="命中 2 处"):
        await _tool(tools, "space_edit").handler(
            _ctx(), {"path": "a.md", "old": "同一行", "new": "改"}
        )
    result = await _confirmed(
        _tool(tools, "space_edit"),
        _ctx(),
        {"path": "a.md", "old": "同一行", "new": "改", "all": True},
    )
    assert result["replaced"] == 2
    assert store.read("a.md")["content"] == "改\n改\n"


@pytest.mark.asyncio
async def test_delete_requires_confirmation(store: SpaceStore) -> None:
    tools = build_tools(store, lambda: {})
    ctx = _ctx()
    await store.create("a.md", "保留内容", store_limits())

    first = await _tool(tools, "space_delete").handler(ctx, {"path": "a.md"})
    assert first["needs_confirmation"] is True
    assert first["confirm_token"]
    assert "结束本轮" in first["message"]
    assert "确认不代表删除授权" in first["message"]
    assert first["preview"]["path"] == "a.md"
    assert "保留内容" in first["preview"]["preview"]
    assert store.read("a.md")["content"] == "保留内容"

    # token 绑定 session：别人的 token 不能执行
    other = _ctx("private:2")
    second = await _tool(tools, "space_delete").handler(
        other, {"path": "a.md", "confirm_token": first["confirm_token"]}
    )
    assert second["needs_confirmation"] is True
    assert store.read("a.md")["content"] == "保留内容"

    # 同一 session 重新预览并确认，才真正删除
    third = await _tool(tools, "space_delete").handler(ctx, {"path": "a.md"})
    done = await _tool(tools, "space_delete").handler(
        ctx, {"path": "a.md", "confirm_token": third["confirm_token"]}
    )
    assert done["restorable"] is True
    assert not (store.root / "a.md").exists()

    # token 不能复用
    await store.create("b.md", "另一篇", store_limits())
    reused = await _tool(tools, "space_delete").handler(
        ctx, {"path": "b.md", "confirm_token": third["confirm_token"]}
    )
    assert reused["needs_confirmation"] is True


@pytest.mark.asyncio
async def test_sensitive_append_requires_confirmation(store: SpaceStore) -> None:
    tools = build_tools(store, lambda: {})
    ctx = _ctx()
    await store.create("b.md", "B 的记录", store_limits())

    first = await _tool(tools, "space_append").handler(
        ctx,
        {"path": "b.md", "text": "B 承认了某事", "sensitive": True},
    )
    assert first["needs_confirmation"] is True
    assert "B 承认了某事" in first["preview"]["append"]
    assert store.read("b.md")["content"] == "B 的记录"

    done = await _tool(tools, "space_append").handler(
        ctx,
        {
            "path": "b.md",
            "text": "B 承认了某事",
            "sensitive": True,
            "confirm_token": first["confirm_token"],
        },
    )
    assert done["created"] is False
    assert "B 承认了某事" in store.read("b.md")["content"]


@pytest.mark.asyncio
async def test_one_space_across_group_and_private(store: SpaceStore) -> None:
    """手记只有一份：群聊写入，私聊能读到；审计只按会话记 session。"""
    tools = build_tools(store, lambda: {})
    group_ctx = _ctx("group:123", is_group=True)
    private_ctx = _ctx("private:1")

    appended = await _tool(tools, "space_append").handler(
        group_ctx, {"path": "notes/shared.md", "text": "群里记的"}
    )
    assert appended["created"] is True

    read = await _tool(tools, "space_read").handler(
        private_ctx, {"path": "notes/shared.md"}
    )
    assert read["content"] == "群里记的\n"

    listing = await _tool(tools, "space_list").handler(private_ctx, {})
    assert [item["path"] for item in listing["files"]] == ["notes/shared.md"]

    sessions = {item["session"] for item in store.recent_audit(5)}
    assert sessions == {"group:123", "private:1"}


@pytest.mark.asyncio
async def test_create_existing_returns_space_error(store: SpaceStore) -> None:
    tools = build_tools(store, lambda: {})
    ctx = _ctx()
    await _tool(tools, "space_create").handler(ctx, {"path": "a.md", "text": "一"})
    with pytest.raises(SpaceError, match="已存在"):
        await _tool(tools, "space_create").handler(ctx, {"path": "a.md", "text": "二"})
    records = store.recent_audit(5)
    assert records[0]["ok"] is False
    assert "已存在" in records[0]["error"]


@pytest.mark.asyncio
async def test_bad_arguments(store: SpaceStore) -> None:
    ctx = _ctx()
    tools = build_tools(store, lambda: {})
    with pytest.raises(SpaceError, match="path"):
        await _tool(tools, "space_read").handler(ctx, {})
    with pytest.raises(SpaceError, match="text"):
        await _tool(tools, "space_append").handler(ctx, {"path": "a.md", "text": "  "})
    with pytest.raises(SpaceError, match="offset"):
        await _tool(tools, "space_read").handler(ctx, {"path": "a.md", "offset": "abc"})
    with pytest.raises(SpaceError):
        await _tool(tools, "space_list").handler(ctx, {"dir": "../outside"})


@pytest.mark.asyncio
async def test_quota_error_reaches_model_as_exception(store: SpaceStore) -> None:
    settings = {"max_file_kb": 1}
    tools = build_tools(store, lambda: settings)
    with pytest.raises(SpaceError, match="单文件"):
        await _tool(tools, "space_append").handler(
            _ctx(), {"path": "big.md", "text": "x" * 2000}
        )


@pytest.mark.asyncio
async def test_read_chars_setting(store: SpaceStore) -> None:
    await store.create("long.md", "字" * 1500, store_limits())
    settings = {"read_chars": 1000}
    tools = build_tools(store, lambda: settings)
    result = await _tool(tools, "space_read").handler(_ctx(), {"path": "long.md"})
    assert len(result["content"]) == 1000
    assert result["truncated"] is True
    assert result["total_chars"] == 1500


@pytest.mark.asyncio
async def test_search_hits_setting(store: SpaceStore) -> None:
    await store.create("a.md", "命中\n命中\n命中\n", store_limits())
    settings = {"search_hits": 2}
    tools = build_tools(store, lambda: settings)
    result = await _tool(tools, "space_search").handler(_ctx(), {"query": "命中"})
    assert len(result["hits"]) == 2
    assert result["truncated"] is True


def store_limits():
    from core.space import Limits

    return Limits()


def test_schemas_are_openai_functions(store: SpaceStore) -> None:
    tools = build_tools(store, lambda: {})
    for item in tools:
        schema = item.schema()
        assert schema["type"] == "function"
        function = schema["function"]
        assert function["name"] == item.name
        assert function["description"]
        assert function["parameters"]["type"] == "object"
        assert function["parameters"]["additionalProperties"] is False

    read = _tool(tools, "space_read")
    assert read.parameters["required"] == ["path"]
    append = _tool(tools, "space_append")
    assert append.parameters["required"] == ["path", "text"]
    assert append.read_only is False
    edit = _tool(tools, "space_edit")
    assert edit.parameters["required"] == ["path", "old", "new"]
    assert edit.read_only is False
    assert "确认" in edit.description
    assert "删行也属于删除" in edit.description
    delete = _tool(tools, "space_delete")
    assert delete.parameters["required"] == ["path"]
    assert delete.read_only is False
    assert "第三方" in delete.description
    restore = _tool(tools, "space_restore")
    assert restore.parameters["required"] == ["id"]
    assert restore.read_only is False
    purge = _tool(tools, "space_purge")
    assert purge.parameters["required"] == ["id"]
    assert purge.read_only is False
    assert _tool(tools, "space_trash").read_only is True
    assert _tool(tools, "space_list").read_only is True
