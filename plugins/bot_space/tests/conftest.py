"""测试引导：优先用 qq-bot-lite 的真实 app.sdk；不在 PYTHONPATH 时注入最小 stub。

插件发布后可以脱离主项目单独跑测试（CI 就是这种情况），但部署时用的是真实 SDK。
stub 的字段和 docs/plugin.md 的契约保持一致；如果契约变了，这个文件也要跟着改。
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_sdk_stub() -> None:
    app_module = types.ModuleType("app")
    sdk_module = types.ModuleType("app.sdk")

    @dataclass
    class Tool:
        name: str
        description: str
        parameters: dict
        handler: Callable[..., Any]
        read_only: bool = True

        def schema(self) -> dict:
            return {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": self.description,
                    "parameters": self.parameters,
                },
            }

    @dataclass
    class ToolContext:
        plugin: str = ""
        session_key: str = ""
        is_group: bool = False
        is_admin: bool = False
        trigger: str = "chat"
        data_dir: Path | None = None
        log: Any = None

    @dataclass
    class HookContext:
        event: Any = None
        session_key: str = ""
        is_group: bool = False
        history: list = field(default_factory=list)

    class Extension:
        name = "extension"
        ctx: Any = None

        def tools(self) -> list:
            return []

    @dataclass
    class PluginContext:
        name: str
        title: str = ""
        settings: dict = field(default_factory=dict)
        services: dict = field(default_factory=dict)
        data_dir: Path | None = None
        panel_url: str | None = None
        log: Any = None

    sdk_module.Tool = Tool
    sdk_module.ToolContext = ToolContext
    sdk_module.HookContext = HookContext
    sdk_module.Extension = Extension
    sdk_module.PluginContext = PluginContext
    app_module.sdk = sdk_module
    sys.modules.setdefault("app", app_module)
    sys.modules["app.sdk"] = sdk_module


try:
    import app.sdk  # noqa: F401
except ModuleNotFoundError:
    _install_sdk_stub()


class RecordingLog:
    """收集日志行，测试里断言「审计 / 启用」有没有写日志。"""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def _add(self, level: str, args: tuple) -> None:
        self.lines.append((level, " ".join(str(item) for item in args)))

    def debug(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self._add("debug", args)

    def info(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self._add("info", args)

    def warning(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self._add("warning", args)

    def error(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self._add("error", args)


@pytest.fixture()
def plugin_module():
    """按宿主的包加载方式导入 plugin.py，相对 import 才能成立。"""
    import importlib.util

    name = "qqbotlite_plugin_bot_space_test"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "plugin.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(name, None)


@pytest.fixture()
def fake_ctx(tmp_path: Path):
    """最小 PluginContext：setup() 只用到 data_dir / settings / log。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        name="bot_space",
        title="bot_space",
        settings={},
        services={},
        data_dir=tmp_path,
        panel_url="/plugins/bot_space",
        log=RecordingLog(),
    )
