"""插件清单：读 `plugins/<名字>/plugin.toml`。

刻意不导入插件代码：面板列一遍插件不应该执行任何插件逻辑，
装坏的插件也只能显示成一张带错误的卡，不会把整个列表拖垮。
"""

from __future__ import annotations

import asyncio
import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .. import __version__ as APP_VERSION
from . import ENTRY_FILE, META_FILE

# 插件接口版本：改契约就要升它，见 docs/plugin.md
SUPPORTED_API = 1
# 插件 id 会出现在 URL、配置键、日志里，限定成安全字符
_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(slots=True)
class PluginContext:
    """插件能用到的运行时对象，故意给得很少。"""

    name: str
    title: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)
    data_dir: Path | None = None
    panel_url: str | None = None
    # PluginLogger（LoggerAdapter）：日志自动带 [标题] 前缀
    log: Any = field(default_factory=lambda: logging.getLogger("plugin"))
    _tasks: list[asyncio.Task] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def spawn(self, coro) -> asyncio.Task:
        """起一个受管的后台任务，进程关闭时统一取消。"""
        task = asyncio.ensure_future(coro)
        task.add_done_callback(self._task_done)
        self._tasks.append(task)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.log.error("后台任务异常: %s", error, exc_info=error)

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


@dataclass(slots=True)
class PluginManifest:
    name: str
    title: str = ""
    description: str = ""
    version: str = "0.0.0"
    author: str = ""
    repository: str = ""
    config: list[dict[str, Any]] = field(default_factory=list)
    panel: str | None = None
    api: int = SUPPORTED_API
    entry: str = ENTRY_FILE
    app_version: str = ""
    warning: str = ""
    error: str | None = None

    @property
    def display(self) -> str:
        return self.title or self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.display,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "repository": self.repository,
            "config": self.config,
            "panel": self.panel,
            "api": self.api,
            "app_version": self.app_version,
            "warning": self.warning,
            "error": self.error,
        }


def valid_name(name: str) -> bool:
    """插件 id 是否合法：只允许字母数字下划线，且不能是隐藏/模板目录。"""
    return bool(_ID_RE.match(name)) and not name.startswith((".", "_"))


def _app_version_warning(requirement: str) -> str:
    """检查插件声明的 qq-bot-lite 版本范围；只警告，不拦加载。"""
    if not requirement:
        return ""
    try:
        spec = SpecifierSet(requirement)
    except InvalidSpecifier:
        # 容错：只写 "0.2" 时按最低版本 >=0.2 处理
        try:
            spec = SpecifierSet(f">={Version(requirement)}")
        except InvalidVersion:
            return f"app_version 写法不合法：{requirement}"
    try:
        current = Version(APP_VERSION)
    except InvalidVersion:
        return ""
    if current in spec:
        return ""
    return f"要求 qq-bot-lite {requirement}，当前 {APP_VERSION}"


def discover(root: Path) -> list[str]:
    """`plugins/` 下带 plugin.toml 的文件夹就是插件。"""
    if not root.exists():
        return []
    names = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith((".", "_")):
            continue
        if not _ID_RE.match(path.name):
            continue  # id 只允许字母数字下划线，别让它污染 URL 和配置键
        if (path / META_FILE).exists():
            names.append(path.name)
    return names


def metadata_path(root: Path, name: str) -> Path:
    return root / name / META_FILE


def entry_path(root: Path, name: str, entry: str = ENTRY_FILE) -> Path:
    return root / name / entry


def read_manifest(root: Path, name: str) -> PluginManifest:
    path = metadata_path(root, name)
    if not path.exists():
        return PluginManifest(name=name, error=f"缺少 {META_FILE}")
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return PluginManifest(name=name, error=f"{META_FILE} 读不了：{exc}")

    declared_id = str(raw.get("id") or "").strip()
    if declared_id:
        if not valid_name(declared_id):
            return PluginManifest(name=name, error=f"id 不合法：{declared_id}")
        if declared_id != name:
            return PluginManifest(
                name=name,
                error=f"id 与目录名不一致：plugin.toml 写的是 {declared_id}，目录是 {name}",
            )

    repository = str(raw.get("repository") or "").strip()
    if repository and not repository.startswith(("https://", "http://")):
        repository = ""

    entry = str(raw.get("entry") or ENTRY_FILE)
    if not entry_path(root, name, entry).exists():
        return PluginManifest(name=name, error=f"缺少 {entry}")

    api = raw.get("api", SUPPORTED_API)
    try:
        api = int(api)
    except (TypeError, ValueError):
        return PluginManifest(name=name, error=f"api 必须是整数，现在是 {api!r}")
    if api > SUPPORTED_API:
        return PluginManifest(
            name=name,
            error=f"需要 api {api}，本机只支持到 {SUPPORTED_API}，请升级 qq-bot-lite",
        )

    config = raw.get("config") or []
    if not isinstance(config, list):
        config = []
    app_version = str(raw.get("app_version") or "").strip()
    return PluginManifest(
        name=name,
        title=str(raw.get("title") or name),
        description=str(raw.get("description") or ""),
        version=str(raw.get("version") or "0.0.0"),
        author=str(raw.get("author") or ""),
        repository=repository,
        config=[item for item in config if isinstance(item, dict) and item.get("key")],
        panel=str(raw["panel"]) if raw.get("panel") else None,
        api=api,
        entry=entry,
        app_version=app_version,
        warning=_app_version_warning(app_version),
    )

