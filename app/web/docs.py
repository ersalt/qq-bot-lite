"""本地离线文档页：直接读仓库里的 Markdown，不依赖外部站点。"""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .markdown_render import render_markdown

ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs"
PLUGINS_DIR = ROOT / "plugins"

DOC_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "开始",
        [
            ("guide.md", "使用指南"),
            ("settings.md", "设置说明"),
        ],
    ),
    (
        "开发",
        [
            ("development.md", "开发文档"),
            ("api.md", "SDK 与 API"),
            ("plugin.md", "插件规范"),
            ("plugin-integration.md", "插件协作"),
            ("local-first.md", "本地优先取舍"),
            ("ui-spec.md", "界面规范"),
            ("log-spec.md", "日志格式"),
        ],
    ),
    (
        "内置插件",
        [
            ("plugins/whale_lexicon/README.md", "鲸语汇"),
            ("plugins/bot_space/README.md", "BOT空间"),
        ],
    ),
]

_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_HREF_RE = re.compile(r'href="([^"]*)"')


def _url_rel(path: Path) -> str:
    """文档在文档页签里的相对路径。"""
    try:
        return path.relative_to(DOCS_DIR).as_posix()
    except ValueError:
        return path.relative_to(ROOT).as_posix()


def _resolve(name: str) -> Path | None:
    raw = name.replace("\\", "/").strip("/")
    if not raw:
        raw = "guide.md"
    if not raw.endswith(".md"):
        raw += ".md"
    if raw.startswith("plugins/"):
        base = PLUGINS_DIR.resolve()
        candidate = (ROOT / raw).resolve()
    else:
        base = DOCS_DIR.resolve()
        candidate = (DOCS_DIR / raw).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _title(path: Path, text: str) -> str:
    match = _TITLE_RE.search(text)
    return match.group(1).strip() if match else path.stem


def _links(rendered: str, current: Path) -> str:
    """把 Markdown 里的本地 .md 链接改成 #documents/...，其余链接原样保留。"""

    def replace(match: re.Match[str]) -> str:
        target = html.unescape(match.group(1))
        if not target or target.startswith(("http://", "https://", "mailto:", "#", "/")):
            return match.group(0)
        path_part, _, fragment = target.partition("#")
        if not path_part.endswith(".md"):
            return match.group(0)
        candidate = (current.parent / path_part).resolve()
        if not candidate.is_file():
            return match.group(0)
        try:
            inside = candidate.is_relative_to(DOCS_DIR.resolve()) or candidate.is_relative_to(
                PLUGINS_DIR.resolve()
            )
        except ValueError:
            inside = False
        if not inside:
            return match.group(0)
        rel = _url_rel(candidate)
        url = f"#documents/{quote(rel, safe='/')}"
        if fragment:
            url += f"#{fragment}"
        return f'href="{html.escape(url, quote=True)}"'

    return _HREF_RE.sub(replace, rendered)


def _payload(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": _url_rel(path),
        "title": _title(path, text),
        "html": _links(render_markdown(text), path),
    }


def _items() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for group, entries in DOC_GROUPS:
        for rel, label in entries:
            path = _resolve(rel)
            if path is not None:
                items.append(
                    {"group": group, "path": rel, "label": label, "title": _payload(path)["title"]}
                )
    return items


def mount_docs(app: FastAPI) -> None:
    @app.get("/api/docs")
    async def docs_index() -> JSONResponse:
        return JSONResponse({"ok": True, "items": _items(), "default": "guide.md"})

    @app.get("/api/docs/{name:path}")
    async def docs_content(name: str) -> JSONResponse:
        path = _resolve(name)
        if path is None:
            return JSONResponse({"ok": False, "message": "文档不存在"}, status_code=404)
        return JSONResponse({"ok": True, **_payload(path)})
