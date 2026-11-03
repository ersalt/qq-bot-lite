"""插件注册表与钩子分发。

插件是一个文件夹，导出 `setup(ctx)`（返回扩展实例）和可选的 `router`。
约定：钩子抛异常只记日志，绝不影响聊天主流程。
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
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
        extra_dirs: list[Path] | None = None,
    ):
        self.enabled = list(names)
        self.plugin_dir = plugin_dir or PLUGIN_DIR
        self.extra_dirs = [Path(item) for item in (extra_dirs or [])]
        self.data_dir = data_dir or DATA_DIR
        self.services = services or {}
        self.extensions: list[Extension] = []
        self.manifests: dict[str, PluginManifest] = {}
        self.routers: dict[str, Any] = {}
        self.pages: dict[str, Path | None] = {}
        self.errors: dict[str, str] = {}
        self._roots: dict[str, Path] = {}

    # ---------- 清单 ----------

    def scan(self) -> list[PluginManifest]:
        """扫所有插件目录。插件项目可以放在仓库外面，用 [extensions] dirs 配。"""
        found: dict[str, Path] = {}
        for root in [self.plugin_dir, *self.extra_dirs]:
            for name in discover(root):
                found.setdefault(name, root)  # 内建目录优先
        self._roots = found
        self.manifests = {name: read_manifest(root, name) for name, root in found.items()}
        return list(self.manifests.values())

    def root_of(self, name: str) -> Path:
        if not self.manifests:
            self.scan()
        return self._roots.get(name, self.plugin_dir)

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
            item["last_error"] = self.errors.get(name) or item.get("error")
            item["has_page"] = bool(self.pages.get(name))
            items.append(item)
        items.sort(key=lambda item: (not item["enabled"], item["title"].lower()))
        return items

    # ---------- 加载 ----------

    async def load(self, names: list[str] | None = None) -> None:
        self.extensions.clear()
        self.routers.clear()
        self.pages.clear()
        self.errors.clear()
        self.scan()
        for name in names if names is not None else self.enabled:
            await self._load_one(name)

    async def _load_one(self, name: str) -> None:
        root = self.root_of(name)
        manifest = self.manifests.get(name) or read_manifest(root, name)
        if manifest.error:
            log.error("插件 %s 无法加载: %s", name, manifest.error)
            self.errors[name] = manifest.error
            return
        path = entry_path(root, name, manifest.entry)
        try:
            # 让入口所在目录变成包的搜索路径，插件就能 from .core import xxx
            spec = importlib.util.spec_from_file_location(
                f"qqbotlite_plugin_{name}",
                path,
                submodule_search_locations=[str(path.parent)],
            )
            if spec is None or spec.loader is None:
                raise ImportError("无法创建模块")
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"qqbotlite_plugin_{name}"] = module
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001
            log.exception("插件加载失败: %s", name)
            self.errors[name] = f"加载失败：{exc}"
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
            panel_url=manifest.panel,
            log=logging.getLogger(f"plugin.{name}"),
        )
        factory = getattr(module, "setup", None)
        if factory is not None:
            try:
                extension = factory(context)
                if inspect.isawaitable(extension):
                    extension = await extension
            except Exception as exc:  # noqa: BLE001
                log.exception("插件初始化失败: %s", name)
                self.errors[name] = f"初始化失败：{exc}"
                extension = None
            if extension is not None:
                extension.ctx = context
                self.extensions.append(extension)
                await self._lifecycle(extension, "on_start", name)
                log.info("插件已启用: %s", getattr(extension, "name", name))
        elif getattr(module, "Extension", None) is not None:
            log.warning("插件 %s 没有 setup()，钩子不会被调用", name)

        router = getattr(module, "router", None)
        if router is not None:
            self.routers[name] = router

        # 页面约定：插件目录下有 pages/index.html 就能访问 /plugins/<id>/
        page = root / name / "pages" / "index.html"
        self.pages[name] = page if page.exists() else None

    async def _lifecycle(self, extension: Any, hook: str, name: str) -> None:
        handler = getattr(extension, hook, None)
        if handler is None:
            return
        try:
            result = handler()
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 生命周期失败不能拦住别人的插件
            log.exception("插件 %s %s 异常", name, hook)
            self.errors[name] = f"{hook} 异常"

    async def shutdown(self) -> None:
        """关进程时收尾：先给插件机会自己收，再统一取消它们起的任务。"""
        for extension in self.extensions:
            await self._lifecycle(extension, "on_stop", getattr(extension, "name", "?"))
            if extension.ctx is not None:
                await extension.ctx.shutdown()
        self.extensions.clear()

    async def set_enabled(self, name: str, enabled: bool) -> None:
        if enabled and name not in self.enabled:
            self.enabled.append(name)
        elif not enabled and name in self.enabled:
            self.enabled.remove(name)
        await self.load()

    def update_settings(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        manifest = self.manifests.get(name) or read_manifest(self.root_of(name), name)
        defaults = defaults_from(manifest.config)
        merged = {**defaults, **self.settings_of(name, defaults), **values}
        save_settings(self.settings_path(name), merged)
        for extension in self.extensions:
            if extension.ctx and extension.ctx.name == name:
                extension.ctx.settings = dict(merged)
        return merged

    def _mark_error(self, name: str, exc: Exception) -> None:
        """记下第一次出错，面板上能看见。"""
        self.errors.setdefault(name, f"{type(exc).__name__}: {exc}")

    # ---------- 钩子 ----------

    async def on_message(self, ctx: HookContext) -> None:
        for extension in self.extensions:
            try:
                await extension.on_message(ctx)
            except Exception as exc:  # noqa: BLE001
                log.exception("插件 %s on_message 异常", extension.name)
                self._mark_error(extension.name, exc)

    async def collect_system_prompt(self, ctx: HookContext) -> list[str]:
        sections: list[str] = []
        for extension in self.extensions:
            try:
                text = await extension.system_prompt(ctx)
            except Exception as exc:  # noqa: BLE001
                log.exception("插件 %s system_prompt 异常", extension.name)
                self._mark_error(extension.name, exc)
                continue
            if text and text.strip():
                sections.append(text.strip())
        return sections

    async def after_reply(self, ctx: HookContext, text: str) -> str:
        for extension in self.extensions:
            try:
                text = await extension.after_reply(ctx, text)
            except Exception as exc:  # noqa: BLE001
                log.exception("插件 %s after_reply 异常", extension.name)
                self._mark_error(extension.name, exc)
        return text





