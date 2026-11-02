"""插件式扩展点。

一个插件就是 `app/extensions/` 下的一个普通模块，导出 MANIFEST、可选的
`setup(ctx)` 和可选的 `router`。约定见 `manifest.py`。

    class Extension:
        name = "slang"
        async def on_message(self, ctx): ...          # 记录/学习黑话
        async def system_prompt(self, ctx): ...       # 返回一段注入到 system 末尾的文本
        async def after_reply(self, ctx, text): ...   # 改写要发出去的文本

约定：扩展抛异常只记日志，绝不影响聊天主流程。
"""

from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..onebot.events import MessageEvent
from .manifest import PluginContext, PluginManifest, discover, read_manifest
from .settings_store import PluginSettingsStore, defaults_from

log = logging.getLogger(__name__)

EXTENSION_DIR = Path(__file__).resolve().parent


@dataclass(slots=True)
class HookContext:
    event: MessageEvent
    session_key: str
    is_group: bool
    history: list[dict[str, Any]]


class Extension:
    """扩展基类，全部钩子可选。"""

    name: str = "extension"
    ctx: PluginContext | None = None

    async def on_message(self, ctx: HookContext) -> None:  # noqa: D401 - 钩子
        return None

    async def system_prompt(self, ctx: HookContext) -> str | None:
        return None

    async def after_reply(self, ctx: HookContext, text: str) -> str:
        return text


class ExtensionRegistry:
    def __init__(
        self,
        names: list[str],
        *,
        directory: Path | None = None,
        settings: PluginSettingsStore | None = None,
        services: dict[str, Any] | None = None,
    ):
        self.enabled = list(names)
        self.directory = directory or EXTENSION_DIR
        self.settings = settings or PluginSettingsStore(
            (directory or EXTENSION_DIR).parent.parent / "config" / "plugins.toml"
        )
        self.services = services or {}
        self.extensions: list[Extension] = []
        self.manifests: dict[str, PluginManifest] = {}
        self.routers: dict[str, Any] = {}

    def scan(self) -> list[PluginManifest]:
        """扫目录，返回所有插件的元数据（含加载失败的，带 error）。"""
        self.manifests = {name: read_manifest(self.directory, name) for name in discover(self.directory)}
        return list(self.manifests.values())

    def list_plugins(self) -> list[dict[str, Any]]:
        if not self.manifests:
            self.scan()
        items: list[dict[str, Any]] = []
        for name, manifest in self.manifests.items():
            item = manifest.to_dict()
            item["enabled"] = name in self.enabled
            item["settings"] = self.settings.of(name, defaults_from(manifest.config))
            item["loaded"] = any(getattr(ext, "name", None) == name for ext in self.extensions)
            items.append(item)
        items.sort(key=lambda item: (not item["enabled"], item["title"].lower()))
        return items

    async def load(self, names: list[str] | None = None) -> None:
        self.extensions.clear()
        self.routers.clear()
        self.scan()
        for name in names if names is not None else self.enabled:
            await self._load_one(name)

    async def _load_one(self, name: str) -> None:
        manifest = self.manifests.get(name) or read_manifest(self.directory, name)
        if manifest.error:
            log.error("插件 %s 无法加载: %s", name, manifest.error)
            return
        try:
            module = importlib.import_module(f"app.extensions.{name}")
        except Exception:  # noqa: BLE001
            log.exception("扩展加载失败: %s", name)
            return

        context = PluginContext(
            name=name,
            settings=self.settings.of(name, defaults_from(manifest.config)),
            services=self.services,
        )
        try:
            extension = await _instantiate(module, context)
        except Exception:  # noqa: BLE001
            log.exception("扩展初始化失败: %s", name)
            return
        if extension is not None:
            extension.ctx = context
            self.extensions.append(extension)
            log.info("扩展已启用: %s", getattr(extension, "name", name))

        router = getattr(module, "router", None)
        if router is not None:
            self.routers[name] = router

    async def set_enabled(self, name: str, enabled: bool) -> None:
        if enabled and name not in self.enabled:
            self.enabled.append(name)
        elif not enabled and name in self.enabled:
            self.enabled.remove(name)
        await self.load()

    def update_settings(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        manifest = self.manifests.get(name) or read_manifest(self.directory, name)
        defaults = defaults_from(manifest.config)
        merged = {**defaults, **self.settings.of(name, defaults), **values}
        self.settings.save(name, merged)
        for extension in self.extensions:
            if extension.ctx and extension.ctx.name == name:
                extension.ctx.settings = dict(merged)
        return merged

    # ---------- 钩子 ----------

    async def on_message(self, ctx: HookContext) -> None:
        for extension in self.extensions:
            try:
                await extension.on_message(ctx)
            except Exception:  # noqa: BLE001
                log.exception("扩展 %s on_message 异常", extension.name)

    async def collect_system_prompt(self, ctx: HookContext) -> list[str]:
        sections: list[str] = []
        for extension in self.extensions:
            try:
                text = await extension.system_prompt(ctx)
            except Exception:  # noqa: BLE001
                log.exception("扩展 %s system_prompt 异常", extension.name)
                continue
            if text and text.strip():
                sections.append(text.strip())
        return sections

    async def after_reply(self, ctx: HookContext, text: str) -> str:
        for extension in self.extensions:
            try:
                text = await extension.after_reply(ctx, text)
            except Exception:  # noqa: BLE001
                log.exception("扩展 %s after_reply 异常", extension.name)
        return text


async def _instantiate(module: Any, context: PluginContext) -> Extension | None:
    factory = getattr(module, "setup", None)
    if factory is not None:
        result = factory(context)
        if inspect.isawaitable(result):
            result = await result
        return result
    candidate = getattr(module, "Extension", None)
    if isinstance(candidate, type) and candidate is not Extension:
        return candidate()
    return None

