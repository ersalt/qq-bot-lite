"""bot_space 的纯逻辑：路径、配额、原子写、审计、搜索。

这里只依赖标准库，不 import app.*：宿主目录、日志和配额都由外面传进来，
所以这一层可以脱离机器人单独测试。

安全边界写死在代码里：

- 根目录固定，模型只能传相对路径；
- 只接受 `.md`，最多两层子目录；
- `.`、`..`、盘符、UNC、隐藏文件、Windows 保留名一律拒绝；
- `resolve()` 后必须仍在根目录内，路径段出现符号链接/junction 就拒绝；
- 单文件、文件数、总量三档配额；
- 写入走同目录临时文件 + `os.replace`，同一路径用 `asyncio.Lock` 串行。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

MD_SUFFIX = ".md"
MAX_DEPTH = 3  # 根目录之下最多两层子目录 + 文件名
MAX_SEGMENT = 60
MAX_REL_CHARS = 160
MAX_TEXT_CHARS = 200_000
MAX_READ_BYTES = 2 * 1024 * 1024
AUDIT_NAME = ".audit.jsonl"
AUDIT_ROTATE_BYTES = 2 * 1024 * 1024
AUDIT_KEEP_LINES = 1000
AUDIT_MAX_RECORDS = 200
TITLE_LIMIT = 60
TRASH_DIR = ".trash"
TRASH_META = "meta.json"
TRASH_CONTENT = "content.md"
TRASH_LIST_LIMIT = 40
TRASH_MAX_ITEMS = 200
TRASH_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")

WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}

_SPACES = re.compile(r"\s+")
_INVALID_CHARS = re.compile(r"[^\w\-]+", re.UNICODE)
_DASHES = re.compile(r"-{2,}")
_HEADING = re.compile(r"^#{1,6}\s+")


class SpaceError(ValueError):
    """手记操作失败；ToolRunner 会把它转成 `ok: false` 回给模型。"""

    def __init__(self, message: str, *, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


def _as_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    raise SpaceError("路径必须是字符串")


def _check_relative(text: str) -> None:
    if not text:
        raise SpaceError("路径不能为空")
    if len(text) > MAX_REL_CHARS:
        raise SpaceError(f"路径太长（最多 {MAX_REL_CHARS} 个字符）")
    if "\\" in text:
        raise SpaceError("路径用 / 分隔，不能包含反斜杠")
    if ":" in text:
        raise SpaceError("路径不能包含冒号")
    if any(ord(char) < 32 for char in text):
        raise SpaceError("路径不能包含控制字符")
    if text.startswith(("/", "~")):
        raise SpaceError("只接受相对路径，不能以 / 或 ~ 开头")


def _slug_segment(segment: str) -> str:
    """把一段文件名/目录名清洗成稳定的 slug：保留中文和字母数字，其余转 -。"""
    text = unicodedata.normalize("NFKC", segment).strip().rstrip(" .")
    text = _SPACES.sub("-", text)
    text = _INVALID_CHARS.sub("-", text)
    text = _DASHES.sub("-", text).strip("- ")
    text = text.lower()
    if not text:
        raise SpaceError(f"名字清洗后为空：{segment!r}")
    if len(text) > MAX_SEGMENT:
        raise SpaceError(f"名字太长（最多 {MAX_SEGMENT} 个字符）：{segment!r}")
    return text


def _check_reserved(slug: str, original: str) -> None:
    if slug in WINDOWS_RESERVED:
        raise SpaceError(f"Windows 保留名不能用作文件名：{original}")


def _slug_file(name: str) -> str:
    if not name.lower().endswith(MD_SUFFIX):
        raise SpaceError("只允许 .md 文件")
    stem = name[: -len(MD_SUFFIX)]
    if not stem:
        raise SpaceError("文件名不能为空")
    if stem != stem.rstrip(" ."):
        raise SpaceError("文件名不能以空格或点结尾")
    slug = _slug_segment(stem)
    _check_reserved(slug, name)
    return slug + MD_SUFFIX


def normalize_path(raw: Any) -> str:
    """校验并规范化模型传来的文件路径，返回 `dir/name.md` 形式。

    不存在的路径也可以规范化；真正的目录检查在 `SpaceStore._resolve` 里做。
    """
    text = _as_text(raw)
    _check_relative(text)
    text = unicodedata.normalize("NFKC", text)
    parts = text.split("/")
    if len(parts) > MAX_DEPTH:
        raise SpaceError(f"最多两层子目录（路径最多 {MAX_DEPTH} 段）")
    cleaned: list[str] = []
    for index, part in enumerate(parts):
        if not part:
            raise SpaceError("路径不能有空段（连续的 / 或结尾的 /）")
        if part in (".", ".."):
            raise SpaceError("路径不能包含 . 或 ..")
        if part.startswith("."):
            raise SpaceError("不能创建或读取隐藏文件")
        if part != part.rstrip(" ."):
            raise SpaceError("文件名或目录名不能以空格或点结尾")
        if index == len(parts) - 1:
            cleaned.append(_slug_file(part))
        else:
            slug = _slug_segment(part)
            _check_reserved(slug, part)
            cleaned.append(slug)
    return "/".join(cleaned)


def normalize_dir(raw: Any) -> str:
    """校验并规范化目录参数；空字符串表示根目录。"""
    text = _as_text(raw)
    if text in ("", ".", "/"):
        return ""
    text = text.rstrip("/")
    _check_relative(text)
    text = unicodedata.normalize("NFKC", text)
    parts = text.split("/")
    if len(parts) > MAX_DEPTH - 1:
        raise SpaceError("目录最多两层")
    cleaned: list[str] = []
    for part in parts:
        if not part:
            raise SpaceError("目录不能有空段")
        if part in (".", ".."):
            raise SpaceError("目录不能包含 . 或 ..")
        if part.startswith("."):
            raise SpaceError("不能读取隐藏目录")
        if part != part.rstrip(" ."):
            raise SpaceError("目录名不能以空格或点结尾")
        slug = _slug_segment(part)
        _check_reserved(slug, part)
        cleaned.append(slug)
    return "/".join(cleaned)


def title_from_text(text: str, *, limit: int = TITLE_LIMIT) -> str:
    """从 Markdown 正文猜一个标题：第一个非空行，去掉 # 前缀，跳过代码块。"""
    in_code = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line:
            continue
        line = _HEADING.sub("", line)
        line = line.strip("*_`># ").strip()
        if not line:
            continue
        return line[:limit] + ("…" if len(line) > limit else "")
    return ""


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_link(path: Path) -> bool:
    """符号链接或 Windows junction；查不了就当作不安全。"""
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        return bool(checker()) if callable(checker) else False
    except OSError:
        return True


def _int_value(values: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    raw = values.get(key, default)
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class Limits:
    """一次操作的配额；由插件设置在运行时换算。"""

    max_file_bytes: int = 64 * 1024
    max_files: int = 200
    max_total_bytes: int = 10 * 1024 * 1024
    max_read_chars: int = 6000
    max_search_hits: int = 5

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None) -> "Limits":
        values = settings or {}
        return cls(
            max_file_bytes=_int_value(values, "max_file_kb", 64, 1, 1024) * 1024,
            max_files=_int_value(values, "max_files", 200, 1, 2000),
            max_total_bytes=_int_value(values, "max_total_mb", 10, 1, 100) * 1024 * 1024,
            max_read_chars=_int_value(values, "read_chars", 6000, 1000, 8000),
            max_search_hits=_int_value(values, "search_hits", 5, 1, 20),
        )


class SpaceStore:
    """固定根目录下的 Markdown 手记。所有路径参数都先过 `normalize_*`。"""

    def __init__(self, root: Path, *, log: Any = None) -> None:
        self.root = Path(root)
        self.log = log
        self._prepared = False
        self._locks: dict[str, asyncio.Lock] = {}
        self._trash_lock = asyncio.Lock()
        self._audit_writes = 0

    # ---------- 基础设施 ----------

    def ensure(self) -> None:
        """建根目录；重复调用安全。"""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.root = self.root.resolve()
        except OSError as exc:
            raise SpaceError(f"手记目录不可用：{exc}") from exc
        self._prepared = True

    def _ready(self) -> Path:
        if not self._prepared:
            self.ensure()
        return self.root

    @property
    def audit_path(self) -> Path:
        return self.root / AUDIT_NAME

    def _lock(self, rel: str) -> asyncio.Lock:
        lock = self._locks.get(rel)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[rel] = lock
        return lock

    def _resolve(self, rel: str) -> Path:
        root = self._ready()
        target = root.joinpath(*rel.split("/"))
        try:
            resolved = target.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise SpaceError(f"路径不可用：{exc}") from exc
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise SpaceError("路径越界") from exc
        current = root
        for part in rel.split("/"):
            current = current / part
            if current.exists() and _is_link(current):
                raise SpaceError("路径里不能有符号链接或联接点")
        return target

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _iter_md(self, base: Path | None = None) -> Iterator[Path]:
        folder = base or self.root
        for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
            current = Path(dirpath)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if not name.startswith(".") and not _is_link(current / name)
            )
            for name in sorted(filenames):
                if name.startswith(".") or not name.lower().endswith(MD_SUFFIX):
                    continue
                path = current / name
                if not _is_link(path):
                    yield path

    def _iter_trash_content(self) -> Iterator[Path]:
        trash = self.root / TRASH_DIR
        if not trash.is_dir():
            return
        for folder in sorted(trash.iterdir()):
            if not folder.is_dir() or not TRASH_ID_RE.match(folder.name):
                continue
            content = folder / TRASH_CONTENT
            if content.is_file() and not _is_link(content):
                yield content

    def _iter_all_md(self) -> Iterator[Path]:
        """活跃文件 + 回收站文件；配额按两者一起算，回收站不能无限膨胀。"""
        yield from self._iter_md()
        yield from self._iter_trash_content()

    def _sorted_md(self) -> list[Path]:
        paths = list(self._iter_md())

        def mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        paths.sort(key=mtime, reverse=True)
        return paths

    def _title_of(self, path: Path) -> str:
        try:
            with path.open("rb") as handle:
                raw = handle.read(4096)
        except OSError:
            return ""
        return title_from_text(raw.decode("utf-8", errors="replace"))

    def _file_info(self, path: Path) -> dict[str, Any]:
        try:
            stat = path.stat()
        except OSError as exc:
            raise SpaceError(f"读不到文件信息：{exc}") from exc
        return {
            "path": self._relative(path),
            "size": stat.st_size,
            "modified": _iso(stat.st_mtime),
        }

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        """同目录临时文件 + os.replace；失败不留半截文件。"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle_fd, temp_name = tempfile.mkstemp(
                prefix=".tmp-", suffix=path.suffix or ".tmp", dir=str(path.parent)
            )
        except OSError as exc:
            raise SpaceError(f"目录创建失败：{exc}") from exc
        try:
            with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except OSError as exc:
            raise SpaceError(f"写入失败：{exc}") from exc
        finally:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass

    @staticmethod
    def _check_file_size(size: int, limits: Limits) -> None:
        if size > limits.max_file_bytes:
            limit_kb = limits.max_file_bytes // 1024
            raise SpaceError(
                f"单文件最多 {limit_kb}KB，这次写入会到 {size / 1024:.1f}KB；"
                "请拆成多篇或调大配额"
            )

    @staticmethod
    def _check_limits(limits: Limits, *, projected_files: int, projected_bytes: int) -> None:
        if projected_files > limits.max_files:
            raise SpaceError(
                f"文件数最多 {limits.max_files} 篇，已经用满；"
                "回收站也计入配额，模型移入回收站不会释放；需要用户清理数据或调大配额"
            )
        if projected_bytes > limits.max_total_bytes:
            limit_mb = limits.max_total_bytes // 1048576
            raise SpaceError(
                f"手记总量最多 {limit_mb}MB（含回收站），已经用满；"
                "需要用户清理数据或调大配额"
            )

    # ---------- 查询 ----------

    def stats(self, limits: Limits) -> dict[str, Any]:
        self._ready()
        live = list(self._iter_md())
        trash = list(self._iter_trash_content())
        live_bytes = 0
        trash_bytes = 0
        latest = 0.0
        for path in live:
            try:
                stat = path.stat()
            except OSError:
                continue
            live_bytes += stat.st_size
            latest = max(latest, stat.st_mtime)
        for path in trash:
            try:
                stat = path.stat()
            except OSError:
                continue
            trash_bytes += stat.st_size
            latest = max(latest, stat.st_mtime)
        return {
            "files": len(live),
            "bytes": live_bytes,
            "trash_files": len(trash),
            "trash_bytes": trash_bytes,
            "total_files": len(live) + len(trash),
            "total_bytes": live_bytes + trash_bytes,
            "max_files": limits.max_files,
            "max_total_bytes": limits.max_total_bytes,
            "max_file_bytes": limits.max_file_bytes,
            "updated": _iso(latest) if latest else "",
        }

    def list_files(self, subdir: Any = "", *, limit: int = 60) -> dict[str, Any]:
        self._ready()
        folder = normalize_dir(subdir)
        base = self.root
        if folder:
            base = self._resolve(folder)
            if not base.is_dir():
                raise SpaceError(f"目录不存在：{folder}", code="missing")
        rows: list[tuple[float, dict[str, Any]]] = []
        total_bytes = 0
        for path in self._iter_md(base):
            try:
                stat = path.stat()
            except OSError:
                continue
            total_bytes += stat.st_size
            rows.append(
                (
                    stat.st_mtime,
                    {
                        "path": self._relative(path),
                        "title": self._title_of(path),
                        "size": stat.st_size,
                        "modified": _iso(stat.st_mtime),
                    },
                )
            )
        rows.sort(key=lambda row: row[0], reverse=True)
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 60
        files = [info for _, info in rows[:limit]]
        return {
            "dir": folder,
            "files": files,
            "total": len(rows),
            "bytes": total_bytes,
            "truncated": len(rows) > limit,
        }

    def read(self, raw_path: Any, *, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        self._ready()
        rel = normalize_path(raw_path)
        path = self._resolve(rel)
        if not path.exists():
            raise SpaceError(f"文件不存在：{rel}", code="missing")
        if not path.is_file():
            raise SpaceError(f"不是文件：{rel}")
        try:
            stat = path.stat()
        except OSError as exc:
            raise SpaceError(f"读取失败：{exc}") from exc
        if stat.st_size > MAX_READ_BYTES:
            raise SpaceError(f"文件超过 {MAX_READ_BYTES // 1024 // 1024}MB，不读取")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SpaceError(f"读取失败：{exc}") from exc
        total = len(text)
        try:
            start = max(0, int(offset))
        except (TypeError, ValueError) as exc:
            raise SpaceError("offset 必须是整数") from exc
        start = min(start, total)
        if limit is None:
            size = total
        else:
            try:
                size = max(1, int(limit))
            except (TypeError, ValueError) as exc:
                raise SpaceError("limit 必须是整数") from exc
        chunk = text[start : start + size]
        return {
            "path": rel,
            "content": chunk,
            "offset": start,
            "total_chars": total,
            "truncated": start + len(chunk) < total,
            "size": stat.st_size,
            "modified": _iso(stat.st_mtime),
        }

    def search(self, query: Any, *, limit: int = 5) -> dict[str, Any]:
        self._ready()
        needle = str(query or "").strip()
        if not needle:
            raise SpaceError("query 不能为空")
        if len(needle) > 100:
            raise SpaceError("query 太长（最多 100 字）")
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 5
        folded = needle.casefold()
        hits: list[dict[str, Any]] = []
        truncated = False
        for path in self._sorted_md():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = self._relative(path)
            for number, line in enumerate(text.splitlines(), 1):
                index = line.casefold().find(folded)
                if index < 0:
                    continue
                hits.append({"path": rel, "line": number, "text": _snippet(line, index)})
                if len(hits) >= limit:
                    truncated = True
                    break
            if truncated:
                break
        return {"query": needle, "hits": hits, "total": len(hits), "truncated": truncated}

    # ---------- 写入 ----------

    async def create(self, raw_path: Any, text: Any, limits: Limits) -> dict[str, Any]:
        self._ready()
        rel = normalize_path(raw_path)
        body = _require_text(text)
        payload = body.encode("utf-8")
        async with self._lock(rel):
            path = self._resolve(rel)
            if path.exists():
                raise SpaceError(f"已存在：{rel}；要补充内容请用 space_append", code="exists")
            files = list(self._iter_all_md())
            total = _total_bytes(files)
            self._check_limits(
                limits,
                projected_files=len(files) + 1,
                projected_bytes=total + len(payload),
            )
            self._check_file_size(len(payload), limits)
            self._write_atomic(path, body)
            return {**self._file_info(path), "created": True, "chars": len(body)}

    async def append(self, raw_path: Any, text: Any, limits: Limits) -> dict[str, Any]:
        self._ready()
        rel = normalize_path(raw_path)
        body_text = _require_text(text)
        async with self._lock(rel):
            path = self._resolve(rel)
            created = not path.exists()
            existing = ""
            old_size = 0
            if not created:
                if not path.is_file():
                    raise SpaceError(f"不是文件：{rel}")
                try:
                    stat = path.stat()
                    if stat.st_size > MAX_READ_BYTES:
                        raise SpaceError(f"文件超过 {MAX_READ_BYTES // 1024 // 1024}MB，不追加")
                    existing = path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    raise SpaceError(f"读取失败：{exc}") from exc
                old_size = stat.st_size
                if existing and not existing.endswith("\n"):
                    existing += "\n"
            body = existing + body_text
            if not body.endswith("\n"):
                body += "\n"
            payload = body.encode("utf-8")
            self._check_file_size(len(payload), limits)
            files = list(self._iter_all_md())
            total = _total_bytes(files)
            self._check_limits(
                limits,
                projected_files=len(files) + (1 if created else 0),
                projected_bytes=total - old_size + len(payload),
            )
            self._write_atomic(path, body)
            return {**self._file_info(path), "created": created, "chars": len(body_text)}

    async def edit(
        self,
        raw_path: Any,
        old: Any,
        new: Any,
        limits: Limits,
        *,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        """精确替换；old 必须非空，命中多处时要求 replace_all。"""
        self._ready()
        rel = normalize_path(raw_path)
        if not isinstance(old, str) or not old:
            raise SpaceError("old 不能为空")
        if not isinstance(new, str):
            raise SpaceError("new 必须是字符串")
        if len(new) > MAX_TEXT_CHARS:
            raise SpaceError(f"new 太长（最多 {MAX_TEXT_CHARS} 字）")
        async with self._lock(rel):
            path = self._resolve(rel)
            if not path.exists():
                raise SpaceError(f"文件不存在：{rel}", code="missing")
            if not path.is_file():
                raise SpaceError(f"不是文件：{rel}")
            try:
                stat = path.stat()
                if stat.st_size > MAX_READ_BYTES:
                    raise SpaceError(f"文件超过 {MAX_READ_BYTES // 1024 // 1024}MB，不修改")
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise SpaceError(f"读取失败：{exc}") from exc
            count = text.count(old)
            if count == 0:
                raise SpaceError("没有找到 old 对应的文本；请先 space_read 再精确复制要改的片段")
            if count > 1 and not replace_all:
                raise SpaceError(
                    f"old 命中 {count} 处，可能改错；请提供更长的上下文，或 all=true 全部替换"
                )
            body = text.replace(old, new) if replace_all else text.replace(old, new, 1)
            payload = body.encode("utf-8")
            self._check_file_size(len(payload), limits)
            files = list(self._iter_all_md())
            total = _total_bytes(files)
            self._check_limits(
                limits,
                projected_files=len(files),
                projected_bytes=total - stat.st_size + len(payload),
            )
            self._write_atomic(path, body)
            return {
                **self._file_info(path),
                "replaced": count if replace_all else 1,
                "chars": len(new),
            }

    async def delete(self, raw_path: Any) -> dict[str, Any]:
        """软删除到 .trash/<id>/，保留原路径和删除时间，可用 restore 反悔。"""
        self._ready()
        rel = normalize_path(raw_path)
        async with self._trash_lock:
            async with self._lock(rel):
                path = self._resolve(rel)
                if not path.exists():
                    raise SpaceError(f"文件不存在：{rel}", code="missing")
                if not path.is_file():
                    raise SpaceError(f"不是文件：{rel}")
                try:
                    stat = path.stat()
                except OSError as exc:
                    raise SpaceError(f"读取失败：{exc}") from exc
                item_id = self._new_trash_id()
                deleted_at = _now_iso()
                folder = self.root / TRASH_DIR / item_id
                meta = {
                    "id": item_id,
                    "path": rel,
                    "size": stat.st_size,
                    "deleted_at": deleted_at,
                }
                try:
                    folder.mkdir(parents=True, exist_ok=False)
                    self._write_atomic(
                        folder / TRASH_META,
                        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                    )
                    os.replace(path, folder / TRASH_CONTENT)
                except SpaceError:
                    shutil.rmtree(folder, ignore_errors=True)
                    raise
                except OSError as exc:
                    shutil.rmtree(folder, ignore_errors=True)
                    raise SpaceError(f"移入回收站失败：{exc}") from exc
                self._cleanup_empty_dirs(path.parent)
                return {
                    "id": item_id,
                    "path": rel,
                    "size": stat.st_size,
                    "deleted_at": deleted_at,
                    "restorable": True,
                }

    def trash_items(self, limit: int = TRASH_LIST_LIMIT) -> dict[str, Any]:
        self._ready()
        try:
            limit = max(1, min(int(limit), TRASH_MAX_ITEMS))
        except (TypeError, ValueError):
            limit = TRASH_LIST_LIMIT
        trash = self.root / TRASH_DIR
        items: list[dict[str, Any]] = []
        if trash.is_dir():
            for folder in trash.iterdir():
                if not folder.is_dir() or not TRASH_ID_RE.match(folder.name):
                    continue
                meta = _read_json(folder / TRASH_META)
                content = folder / TRASH_CONTENT
                if not isinstance(meta, dict) or not content.is_file():
                    continue
                raw_path = meta.get("path")
                if not isinstance(raw_path, str):
                    continue
                try:
                    rel = normalize_path(raw_path)
                    size = content.stat().st_size
                except (SpaceError, OSError):
                    continue
                items.append(
                    {
                        "id": folder.name,
                        "path": rel,
                        "size": size,
                        "deleted_at": str(meta.get("deleted_at") or ""),
                    }
                )
        items.sort(key=lambda item: item["deleted_at"], reverse=True)
        return {
            "items": items[:limit],
            "total": len(items),
            "truncated": len(items) > limit,
        }

    async def restore(self, item_id: Any, limits: Limits) -> dict[str, Any]:
        self._ready()
        ident = str(item_id or "").strip()
        if not TRASH_ID_RE.match(ident):
            raise SpaceError("回收站 id 不合法")
        folder = self.root / TRASH_DIR / ident
        meta = _read_json(folder / TRASH_META)
        if not isinstance(meta, dict):
            raise SpaceError(f"回收站记录不存在或损坏：{ident}", code="missing")
        rel = normalize_path(meta.get("path"))
        source = folder / TRASH_CONTENT
        if not source.is_file():
            raise SpaceError(f"回收站文件不存在：{ident}", code="missing")
        try:
            self._check_file_size(source.stat().st_size, limits)
        except OSError as exc:
            raise SpaceError(f"读取回收站文件失败：{exc}") from exc
        async with self._trash_lock:
            async with self._lock(rel):
                target = self._resolve(rel)
                if target.exists():
                    raise SpaceError(
                        f"恢复失败：{rel} 已存在；先处理现有文件再恢复",
                        code="exists",
                    )
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, target)
                except OSError as exc:
                    raise SpaceError(f"恢复失败：{exc}") from exc
                for leftover in (folder / TRASH_META,):
                    try:
                        leftover.unlink(missing_ok=True)
                    except OSError:
                        pass
                try:
                    folder.rmdir()
                except OSError:
                    pass
                return {**self._file_info(target), "id": ident, "restored": True}

    async def purge(self, item_id: Any) -> dict[str, Any]:
        """永久删除回收站条目；不可恢复，并释放配额。"""
        self._ready()
        ident = str(item_id or "").strip()
        if not TRASH_ID_RE.match(ident):
            raise SpaceError("回收站 id 不合法")
        folder = self.root / TRASH_DIR / ident
        async with self._trash_lock:
            if not folder.is_dir():
                raise SpaceError(f"回收站记录不存在：{ident}", code="missing")
            meta = _read_json(folder / TRASH_META)
            rel = ""
            size = 0
            if isinstance(meta, dict):
                try:
                    rel = normalize_path(meta.get("path"))
                except SpaceError:
                    rel = str(meta.get("path") or "")
                raw_size = meta.get("size")
                if isinstance(raw_size, int):
                    size = raw_size
            try:
                content = folder / TRASH_CONTENT
                if content.is_file():
                    size = content.stat().st_size
                shutil.rmtree(folder)
            except OSError as exc:
                raise SpaceError(f"永久删除失败：{exc}") from exc
            return {"id": ident, "path": rel, "size": size, "purged": True}

    def _new_trash_id(self) -> str:
        trash = self.root / TRASH_DIR
        for _ in range(5):
            item_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
            if not (trash / item_id).exists():
                return item_id
        raise SpaceError("回收站暂时不可用，请稍后再试")

    def _cleanup_empty_dirs(self, folder: Path) -> None:
        current = folder
        while current != self.root:
            try:
                if any(current.iterdir()):
                    return
                current.rmdir()
            except OSError:
                return
            current = current.parent

    # ---------- 审计 ----------

    def audit(
        self,
        tool: str,
        path: str,
        chars: int,
        ok: bool,
        *,
        session: str = "",
        error: str = "",
    ) -> None:
        """记一行 JSONL；不记录正文，只记工具名、路径、字数、成败。"""
        record: dict[str, Any] = {
            "ts": _now_iso(),
            "tool": tool,
            "path": path,
            "chars": int(chars),
            "ok": bool(ok),
            "session": session,
        }
        if error:
            record["error"] = error[:200]
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            self._ready()
            with self.audit_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
            self._audit_writes += 1
            if self._audit_writes % 200 == 0:
                self._trim_audit()
        except OSError as exc:
            if self.log is not None:
                self.log.warning("审计写入失败：%s", exc)
        if self.log is not None:
            state = "成功" if ok else "失败"
            detail = f"（{error[:80]}）" if error else ""
            self.log.info("%s %s %d字 %s%s", tool, path or "-", chars, state, detail)

    def recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        self._ready()
        try:
            limit = max(1, min(int(limit), AUDIT_MAX_RECORDS))
        except (TypeError, ValueError):
            limit = 50
        if not self.audit_path.exists():
            return []
        try:
            lines = self.audit_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        items: list[dict[str, Any]] = []
        for line in reversed(lines[-limit:]):
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(item, dict):
                items.append(item)
        return items

    def _trim_audit(self) -> None:
        try:
            if not self.audit_path.exists() or self.audit_path.stat().st_size <= AUDIT_ROTATE_BYTES:
                return
            lines = self.audit_path.read_text(encoding="utf-8", errors="replace").splitlines()
            kept = lines[-AUDIT_KEEP_LINES:]
            temp = self.root / (AUDIT_NAME + ".tmp")
            temp.write_text("\n".join(kept) + "\n", encoding="utf-8")
            os.replace(temp, self.audit_path)
        except OSError as exc:
            if self.log is not None:
                self.log.warning("审计轮转失败：%s", exc)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _total_bytes(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _require_text(text: Any) -> str:
    if not isinstance(text, str):
        raise SpaceError("text 必须是字符串")
    if not text.strip():
        raise SpaceError("text 不能为空")
    if len(text) > MAX_TEXT_CHARS:
        raise SpaceError(f"text 太长（最多 {MAX_TEXT_CHARS} 字）")
    return text


def _snippet(line: str, index: int) -> str:
    if len(line) <= 160:
        return line.strip()
    start = max(0, index - 60)
    end = min(len(line), start + 200)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(line) else ""
    return prefix + line[start:end].strip() + suffix
