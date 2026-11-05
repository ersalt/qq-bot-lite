"""纯逻辑：路径清洗、配额、原子写、搜索、审计。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.space import (
    Limits,
    SpaceError,
    SpaceStore,
    normalize_dir,
    normalize_path,
    title_from_text,
)

LIMITS = Limits()


@pytest.fixture()
def store(tmp_path: Path) -> SpaceStore:
    instance = SpaceStore(tmp_path / "space")
    instance.ensure()
    return instance


def test_normalize_path_canonical() -> None:
    assert normalize_path("notes/today.md") == "notes/today.md"
    assert normalize_path("Notes/My Note.md") == "notes/my-note.md"
    assert normalize_path("日记/2026-11-05.MD") == "日记/2026-11-05.md"
    assert normalize_path("  a b.md  ") == "a-b.md"
    assert normalize_path("notes/今天 的 笔记.md") == "notes/今天-的-笔记.md"


def test_normalize_dir() -> None:
    assert normalize_dir("") == ""
    assert normalize_dir("/") == ""
    assert normalize_dir("notes/") == "notes"
    assert normalize_dir("Notes/My Dir") == "notes/my-dir"
    with pytest.raises(SpaceError):
        normalize_dir("notes/sub/deep")
    with pytest.raises(SpaceError):
        normalize_dir("../notes")
    with pytest.raises(SpaceError):
        normalize_dir("notes/.hidden")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "notes",
        "notes/a.txt",
        "../a.md",
        "/a.md",
        "~/.a.md",
        "a\\b.md",
        "C:/a.md",
        "notes/../a.md",
        "notes/./a.md",
        "notes//a.md",
        "notes/",
        "notes/.hidden.md",
        "a/b/c/d.md",
        "con.md",
        "notes/CON.md",
        "a" * 61 + ".md",
        "notes/a .md",
        "notes/a.md.",
        "notes/目录.",
        "a\x00.md",
        "notes/a:b.md",
        "//server/share/a.md",
    ],
)
def test_normalize_path_rejects_unsafe(raw: str) -> None:
    with pytest.raises(SpaceError):
        normalize_path(raw)


def test_normalize_path_rejects_non_string() -> None:
    with pytest.raises(SpaceError):
        normalize_path(123)  # type: ignore[arg-type]


def test_title_from_text() -> None:
    assert title_from_text("# 今天的计划\n正文") == "今天的计划"
    assert title_from_text("\n\n## 标题二\n") == "标题二"
    assert title_from_text("```python\nprint(1)\n```\n真实标题") == "真实标题"
    assert title_from_text("") == ""


@pytest.mark.asyncio
async def test_create_and_read(store: SpaceStore) -> None:
    info = await store.create("notes/today.md", "# 今天\n\n第一行\n", LIMITS)
    assert info["created"] is True
    assert info["path"] == "notes/today.md"
    assert (store.root / "notes" / "today.md").is_file()

    data = store.read("notes/today.md")
    assert data["content"] == "# 今天\n\n第一行\n"
    assert data["total_chars"] == len(data["content"])
    assert data["truncated"] is False
    assert data["path"] == "notes/today.md"


@pytest.mark.asyncio
async def test_create_does_not_overwrite(store: SpaceStore) -> None:
    await store.create("a.md", "原内容", LIMITS)
    with pytest.raises(SpaceError, match="已存在"):
        await store.create("a.md", "新内容", LIMITS)
    assert store.read("a.md")["content"] == "原内容"


@pytest.mark.asyncio
async def test_append_creates_and_separates(store: SpaceStore) -> None:
    first = await store.append("notes/journal.md", "第一段", LIMITS)
    assert first["created"] is True
    second = await store.append("notes/journal.md", "第二段", LIMITS)
    assert second["created"] is False
    assert store.read("notes/journal.md")["content"] == "第一段\n第二段\n"


@pytest.mark.asyncio
async def test_edit_replaces_unique(store: SpaceStore) -> None:
    await store.create("notes/list.md", "1. 贰叔\n2. 饺子\n", LIMITS)
    result = await store.edit("notes/list.md", "2. 饺子\n", "", LIMITS)
    assert result["replaced"] == 1
    assert store.read("notes/list.md")["content"] == "1. 贰叔\n"


@pytest.mark.asyncio
async def test_edit_requires_unique_or_all(store: SpaceStore) -> None:
    await store.create("a.md", "x\nx\n", LIMITS)
    with pytest.raises(SpaceError, match="命中 2 处"):
        await store.edit("a.md", "x", "y", LIMITS)
    result = await store.edit("a.md", "x", "y", LIMITS, replace_all=True)
    assert result["replaced"] == 2
    assert store.read("a.md")["content"] == "y\ny\n"


@pytest.mark.asyncio
async def test_edit_missing_old(store: SpaceStore) -> None:
    await store.create("a.md", "正文", LIMITS)
    with pytest.raises(SpaceError, match="没有找到"):
        await store.edit("a.md", "不存在", "x", LIMITS)


@pytest.mark.asyncio
async def test_delete_and_restore(store: SpaceStore) -> None:
    await store.create("notes/temp.md", "内容", LIMITS)
    deleted = await store.delete("notes/temp.md")
    assert deleted["restorable"] is True
    assert deleted["path"] == "notes/temp.md"
    assert not (store.root / "notes" / "temp.md").exists()
    assert store.list_files()["total"] == 0
    assert store.search("内容")["total"] == 0

    trash = store.trash_items()
    assert trash["total"] == 1
    item = trash["items"][0]
    assert item["id"] == deleted["id"]
    assert item["path"] == "notes/temp.md"

    restored = await store.restore(item["id"], LIMITS)
    assert restored["restored"] is True
    assert restored["path"] == "notes/temp.md"
    assert store.read("notes/temp.md")["content"] == "内容"
    assert store.trash_items()["total"] == 0


@pytest.mark.asyncio
async def test_restore_refuses_existing_target(store: SpaceStore) -> None:
    await store.create("a.md", "旧", LIMITS)
    deleted = await store.delete("a.md")
    await store.create("a.md", "新", LIMITS)
    with pytest.raises(SpaceError, match="已存在"):
        await store.restore(deleted["id"], LIMITS)
    assert store.read("a.md")["content"] == "新"
    assert store.trash_items()["total"] == 1


@pytest.mark.asyncio
async def test_quota_counts_trash(store: SpaceStore) -> None:
    small = Limits(max_files=1)
    await store.create("a.md", "a", small)
    await store.delete("a.md")
    stats = store.stats(small)
    assert stats["files"] == 0
    assert stats["trash_files"] == 1
    assert stats["total_files"] == 1
    with pytest.raises(SpaceError, match="文件数"):
        await store.create("b.md", "b", small)


@pytest.mark.asyncio
async def test_purge_frees_quota(store: SpaceStore) -> None:
    small = Limits(max_files=1)
    await store.create("a.md", "a", small)
    deleted = await store.delete("a.md")
    purged = await store.purge(deleted["id"])
    assert purged["purged"] is True
    assert purged["path"] == "a.md"
    assert store.trash_items()["total"] == 0
    assert store.stats(small)["total_files"] == 0
    await store.create("b.md", "b", small)


@pytest.mark.asyncio
async def test_purge_missing(store: SpaceStore) -> None:
    with pytest.raises(SpaceError) as error:
        await store.purge("20260101-000000-abcdef")
    assert error.value.code == "missing"


@pytest.mark.asyncio
async def test_delete_missing(store: SpaceStore) -> None:
    with pytest.raises(SpaceError) as error:
        await store.delete("nope.md")
    assert error.value.code == "missing"


@pytest.mark.asyncio
async def test_read_offset_and_truncation(store: SpaceStore) -> None:
    await store.create("a.md", "0123456789", LIMITS)
    data = store.read("a.md", offset=3, limit=4)
    assert data["content"] == "3456"
    assert data["offset"] == 3
    assert data["truncated"] is True
    tail = store.read("a.md", offset=8)
    assert tail["content"] == "89"
    assert tail["truncated"] is False


@pytest.mark.asyncio
async def test_read_missing_code(store: SpaceStore) -> None:
    with pytest.raises(SpaceError) as error:
        store.read("nope.md")
    assert error.value.code == "missing"


@pytest.mark.asyncio
async def test_single_file_quota(store: SpaceStore) -> None:
    small = Limits(max_file_bytes=20)
    await store.create("a.md", "x" * 10, small)
    with pytest.raises(SpaceError, match="单文件"):
        await store.append("a.md", "y" * 20, small)
    assert store.read("a.md")["content"] == "x" * 10


@pytest.mark.asyncio
async def test_file_count_quota(store: SpaceStore) -> None:
    small = Limits(max_files=2)
    await store.create("a.md", "a", small)
    await store.create("b.md", "b", small)
    with pytest.raises(SpaceError, match="文件数"):
        await store.create("c.md", "c", small)


@pytest.mark.asyncio
async def test_total_size_quota(store: SpaceStore) -> None:
    small = Limits(max_total_bytes=15)
    await store.create("a.md", "1234567890", small)
    with pytest.raises(SpaceError, match="总量"):
        await store.create("b.md", "abcdefghij", small)


@pytest.mark.asyncio
async def test_list_files_and_titles(store: SpaceStore) -> None:
    await store.create("notes/a.md", "# 计划\n今天写代码\n", LIMITS)
    await store.create("journal/b.md", "# 日记\n今天休息\n", LIMITS)
    (store.root / ".secret.md").write_text("hidden", encoding="utf-8")
    (store.root / "ignore.txt").write_text("not markdown", encoding="utf-8")

    listing = store.list_files()
    paths = {item["path"] for item in listing["files"]}
    assert paths == {"notes/a.md", "journal/b.md"}
    assert listing["total"] == 2
    titles = {item["path"]: item["title"] for item in listing["files"]}
    assert titles["notes/a.md"] == "计划"
    assert titles["journal/b.md"] == "日记"

    sub = store.list_files("notes")
    assert [item["path"] for item in sub["files"]] == ["notes/a.md"]


@pytest.mark.asyncio
async def test_search_returns_paths_and_lines(store: SpaceStore) -> None:
    await store.create("notes/a.md", "# 计划\n今天写代码\n明天也写代码\n", LIMITS)
    await store.create("journal/b.md", "# 日记\n今天休息\n", LIMITS)
    result = store.search("写代码")
    assert result["total"] == 2
    assert {item["path"] for item in result["hits"]} == {"notes/a.md"}
    assert [item["line"] for item in result["hits"]] == [2, 3]
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_search_limit(store: SpaceStore) -> None:
    await store.create("a.md", "命中\n命中\n命中\n", LIMITS)
    result = store.search("命中", limit=2)
    assert len(result["hits"]) == 2
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_audit_jsonl_and_recent(store: SpaceStore) -> None:
    store.audit("space_append", "a.md", 12, True, session="private:1")
    store.audit("space_append", "b.md", 0, False, session="private:1", error="配额满了")
    lines = (store.root / ".audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["tool"] == "space_append"
    assert first["path"] == "a.md"
    assert first["chars"] == 12
    assert first["ok"] is True
    assert first["session"] == "private:1"

    items = store.recent_audit(10)
    assert items[0]["path"] == "b.md"
    assert items[0]["ok"] is False
    assert items[0]["error"] == "配额满了"
    assert items[1]["path"] == "a.md"


@pytest.mark.asyncio
async def test_audit_not_in_file_list(store: SpaceStore) -> None:
    store.audit("space_list", "", 10, True)
    assert store.list_files()["files"] == []


@pytest.mark.asyncio
async def test_symlink_escape_rejected(store: SpaceStore, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    link = store.root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接")
    # Linux 上 resolve 可能先判定为越界；两种错误都说明符号链接逃逸被拒绝
    with pytest.raises(SpaceError, match="符号链接|路径越界"):
        store.read("link/secret.md")


@pytest.mark.asyncio
async def test_atomic_write_leaves_no_temp(store: SpaceStore) -> None:
    await store.create("notes/a.md", "正文", LIMITS)
    leftovers = [path.name for path in (store.root / "notes").iterdir() if path.name.startswith(".tmp-")]
    assert leftovers == []
    assert (store.root / "notes" / "a.md").read_text(encoding="utf-8") == "正文"
