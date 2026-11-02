"""插件清单：读 `plugins/<名字>/plugin.toml`。

刻意不导入插件代码：面板列一遍插件不应该执行任何插件逻辑，
装坏的插件也只能显示成一张带错误的卡，不会把整个列表拖垮。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ENTRY_FILE, META_FILE


@dataclass(slots=True)
class PluginContext:
    """插件能用到的运行时对象，故意给得很少。"""

    name: str
    settings: dict[str, Any] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)
    data_dir: Path | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)


@dataclass(slots=True)
class PluginManifest:
    name: str
    title: str = ""
    description: str = ""
    version: str = "0.0.0"
    author: str = ""
    config: list[dict[str, Any]] = field(default_factory=list)
    panel: str | None = None
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
            "config": self.config,
            "panel": self.panel,
            "error": self.error,
        }


def discover(root: Path) -> list[str]:
    """`plugins/` 下带 plugin.toml 的文件夹就是插件。"""
    if not root.exists():
        return []
    names = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith((".", "_")):
            continue
        if (path / META_FILE).exists():
            names.append(path.name)
    return names


def metadata_path(root: Path, name: str) -> Path:
    return root / name / META_FILE


def entry_path(root: Path, name: str) -> Path:
    return root / name / ENTRY_FILE


def read_manifest(root: Path, name: str) -> PluginManifest:
    path = metadata_path(root, name)
    if not path.exists():
        return PluginManifest(name=name, error=f"缺少 {META_FILE}")
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return PluginManifest(name=name, error=f"{META_FILE} 读不了：{exc}")

    if not entry_path(root, name).exists():
        return PluginManifest(name=name, error=f"缺少 {ENTRY_FILE}")

    config = raw.get("config") or []
    if not isinstance(config, list):
        config = []
    return PluginManifest(
        name=name,
        title=str(raw.get("title") or name),
        description=str(raw.get("description") or ""),
        version=str(raw.get("version") or "0.0.0"),
        author=str(raw.get("author") or ""),
        config=[item for item in config if isinstance(item, dict) and item.get("key")],
        panel=str(raw["panel"]) if raw.get("panel") else None,
    )
