"""插件清单与运行时上下文。

一个插件就是 `app/extensions/` 下的一个模块，导出：

    MANIFEST = {
        "title": "运行统计",
        "description": "在群里发 #统计 看今日数据，也可以在面板里看。",
        "version": "0.1.0",
        "author": "内建",
        "config": [                       # 可选，面板里会生成设置弹窗
            {"key": "window", "label": "统计窗口（小时）", "type": "number", "min": 1, "max": 720},
        ],
        "panel": "/plugins/stats",        # 可选，有值就显示“打开面板”按钮
    }

    def setup(ctx): ...                   # 可选，返回 Extension 实例
    router = APIRouter()                  # 可选，面板后端，挂到 /plugins/<name>
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class PluginContext:
    """插件能用到的运行时对象，故意给得很少。"""

    name: str
    settings: dict[str, Any] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)

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
    builtin: bool = True
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
            "builtin": self.builtin,
            "error": self.error,
        }


RESERVED = {'base', 'manifest', 'settings_store'}


def discover(directory: Path) -> list[str]:
    """目录下所有能当插件加载的模块名。"""
    names: list[str] = []
    for path in sorted(directory.glob("*.py")):
        if path.stem.startswith("_") or path.stem in RESERVED:
            continue
        names.append(path.stem)
    return names


def read_manifest(directory: Path, name: str) -> PluginManifest:
    """只读元数据，不执行 setup()。导入失败也要返回一张能显示错误的卡。"""
    path = directory / f"{name}.py"
    if not path.exists():
        return PluginManifest(name=name, error="文件不存在")
    try:
        spec = importlib.util.spec_from_file_location(f"app.extensions._probe_{name}", path)
        if spec is None or spec.loader is None:
            return PluginManifest(name=name, error="模块无法加载")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 插件写坏了也得能看见
        return PluginManifest(name=name, error=f"{type(exc).__name__}: {exc}")

    raw = getattr(module, "MANIFEST", None)
    if not isinstance(raw, dict):
        return PluginManifest(name=name, error="没有 MANIFEST 字典")
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
        panel=raw.get("panel") or None,
        builtin=bool(raw.get("builtin", True)),
    )

