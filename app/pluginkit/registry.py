"""插件注册表与钩子分发。

插件是一个文件夹，导出 `setup(ctx)`（返回扩展实例）和可选的 `router`。
约定：钩子抛异常只记日志，绝不影响聊天主流程。
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..onebot.events import MessageEvent
from . import DATA_DIR, PLUGIN_DIR
from .manifest import PluginContext, PluginManifest, discover, entry_path, read_manifest
from .settings_store import defaults_from, load as load_settings, save as save_settings

log = logging.getLogger(__name__)


@dataclass(slots=True)
class HookContext:
    event: MessageEvent
    session_key: str
    is_group: bool
    history: list[dict[str, Any]] = field(default_factory=list)


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
        plugin_dir: Path | None = None,
        data_dir: Path | None = None,
        services: dict[str, Any] | None = None,
    ):
        self.enabled = list(names)
        self.plugin_dir = plugin_dir or PLUGIN_DIR
        self.data_dir = data_dir or DATA_DIR
        self.services = services or {}
        self.extensions: list[Extension] = []
        self.manifests: dict[str, PluginManifest] = {}
        self.routers: dict[str, Any] = {}

    # ---------- 清单 ----------

    def scan(self) -> list[PluginManifest]:
        self.manifests = {name: read_manifest(self.plugin_dir, name) for name in discover(self.plugin_dir)}
        return list(self.manifests.values())

    def settings_path(self, name: str) -> Path:
        return self.data_dir / name / "settings.toml"

    def plugin_data_dir(self, name: str) -> Path:
        return self.data_dir / name

    def settings_of(self, name: str, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(defaults or {})
        merged.update(load_settings(self.settings_path(name)))
        return merged

    def list_plugins(self) -> list[dict[str, Any]]:
        if not self.manifests:
            self.scan()
        items: list[dict[str, Any]] = []
        for name, manifest in self.manifests.items():
            item = manifest.to_dict()
            item["enabled"] = name in self.enabled
            item["settings"] = self.settings_of(name, defaults_from(manifest.config))
            item["loaded"] = any(getattr(ext, "name", None) == name for ext in self.extensions)
            items.append(item)
        items.sort(key=lambda item: (not item["enabled"], item["title"].lower()))
        return items

    # ---------- 加载 ----------

    async def load(self, names: list[str] | None = None) -> None:
        self.extensions.clear()
        self.routers.clear()
        self.scan()
        for name in names if names is not None else self.enabled:
            await self._load_one(name)

    async def _load_one(self, name: str) -> None:
        manifest = self.manifests.get(name) or read_manifest(self.plugin_dir, name)
        if manifest.error:
            log.error("插件 %s 无法加载: %s", name, manifest.error)
            return
        path = entry_path(self.plugin_dir, name)
        try:
            spec = importlib.util.spec_from_file_location(f"qqbotlite_plugin_{name}", path)
            if spec is None or spec.loader is None:
                raise ImportError("无法创建模块")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:  # noqa: BLE001
            log.exception("插件加载失败: %s", name)
            return

        data_dir = self.plugin_data_dir(name)
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.warning("插件数据目录建不出来: %s", data_dir)

        context = PluginContext(
            name=name,
            settings=self.settings_of(name, defaults_from(manifest.config)),
            services=self.services,
            data_dir=data_dir,
        )
        factory = getattr(module, "setup", None)
        if factory is not None:
            try:
                extension = factory(context)
                if inspect.isawaitable(extension):
                    extension = await extension
            except Exception:  # noqa: BLE001
                log.exception("插件初始化失败: %s", name)
                extension = None
            if extension is not None:
                extension.ctx = context
                self.extensions.append(extension)
                log.info("插件已启用: %s", getattr(extension, "name", name))
        elif getattr(module, "Extension", None) is not None:
            log.warning("插件 %s 没有 setup()，钩子不会被调用", name)

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
        manifest = self.manifests.get(name) or read_manifest(self.plugin_dir, name)
        defaults = defaults_from(manifest.config)
        merged = {**defaults, **self.settings_of(name, defaults), **values}
        save_settings(self.settings_path(name), merged)
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
                log.exception("插件 %s on_message 异常", extension.name)

    async def collect_system_prompt(self, ctx: HookContext) -> list[str]:
        sections: list[str] = []
        for extension in self.extensions:
            try:
                text = await extension.system_prompt(ctx)
            except Exception:  # noqa: BLE001
                log.exception("插件 %s system_prompt 异常", extension.name)
                continue
            if text and text.strip():
                sections.append(text.strip())
        return sections

    async def after_reply(self, ctx: HookContext, text: str) -> str:
        for extension in self.extensions:
            try:
                text = await extension.after_reply(ctx, text)
            except Exception:  # noqa: BLE001
                log.exception("插件 %s after_reply 异常", extension.name)
        return text
