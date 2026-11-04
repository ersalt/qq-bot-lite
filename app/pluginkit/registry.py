"""插件注册表与钩子分发。

插件是一个文件夹，导出 `setup(ctx)`（返回扩展实例）和可选的 `router`。
约定：钩子抛异常只记日志，绝不影响聊天主流程。
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..onebot.events import MessageEvent
from . import DATA_DIR, PLUGIN_DIR
from .manifest import PluginContext, PluginManifest, discover, entry_path, read_manifest
from .plugin_log import make_plugin_logger
from .settings_store import defaults_from
from .settings_store import load as load_settings
from .settings_store import save as save_settings

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

    async def user_suffix(self, ctx: HookContext) -> str | None:
        """追加到当前这条用户消息末尾（比塞 system 省 KV 缓存）。"""
        return None

    async def settings_fields(
        self, ctx: "PluginContext", fields: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        """设置面板的兜底渲染接口。

        `fields` 是 plugin.toml 里声明的字段（已转成 dict）。返回新的列表就按新的渲染，
        返回 None / 抛异常就用原样。插件可以拿它补动态选项（比如可用的模型列表）、
        按运行状态隐藏字段，或者干脆换一套布局。
        """
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
        # 命令名 -> (插件 id, Command)
        self.commands: dict[str, tuple[str, Any]] = {}
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

    async def settings_schema(self, name: str) -> list[dict[str, Any]]:
        """设置面板要渲染的字段：清单声明为底，插件可以用 settings_fields 改写。"""
        manifest = self.manifests.get(name) or read_manifest(self.root_of(name), name)
        fields = [dict(field) for field in manifest.config]
        extension = next(
            (ext for ext in self.extensions
             if getattr(getattr(ext, "ctx", None), "name", None) == name),
            None,
        )
        hook = getattr(extension, "settings_fields", None) if extension else None
        if hook is None:
            return fields
        try:
            result = hook(extension.ctx, [dict(field) for field in fields])
            if inspect.isawaitable(result):
                result = await result
        except Exception:  # noqa: BLE001 - 兜底接口自己炸了就用清单里的
            log.exception("插件 %s settings_fields 异常", name)
            return fields
        if isinstance(result, list):
            # 返回空列表 = 这个插件的设置全靠自己的页面，主面板不给通用表单
            return [field for field in result if isinstance(field, dict) and field.get("key")]
        return fields

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
        self.commands.clear()
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
        if manifest.warning:
            log.warning("插件 %s: %s", name, manifest.warning)
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
            title=manifest.display,
            settings=self.settings_of(name, defaults_from(manifest.config)),
            services=self.services,
            data_dir=data_dir,
            panel_url=manifest.panel,
            log=make_plugin_logger(name, manifest.display),
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

        for command in getattr(module, "COMMANDS", []) or []:
            aliases = [command.name, *(getattr(command, "aliases", None) or [])]
            for alias in aliases:
                if alias in self.commands:
                    log.warning("命令 %s 被插件 %s 覆盖", alias, name)
                self.commands[alias] = (name, command)

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

    async def unload(self, name: str) -> None:
        """停掉一个插件：on_stop → 取消它的后台任务 → 摘掉钩子/路由/命令/模块缓存。

        不碰别的插件，热重载和删除都走这里。子模块也要一起从 sys.modules 摘掉，
        否则 `from .core import ...` 会拿到旧代码。
        """
        for extension in list(self.extensions):
            ctx = getattr(extension, "ctx", None)
            if getattr(ctx, "name", None) != name:
                continue
            await self._lifecycle(extension, "on_stop", name)
            if ctx is not None:
                await ctx.shutdown()
            self.extensions.remove(extension)

        self.routers.pop(name, None)
        self.pages.pop(name, None)
        self.commands = {
            alias: entry for alias, entry in self.commands.items() if entry[0] != name
        }
        self.errors.pop(name, None)

        prefix = f"qqbotlite_plugin_{name}"
        for module_name in [
            module for module in sys.modules if module == prefix or module.startswith(prefix + ".")
        ]:
            sys.modules.pop(module_name, None)

    async def reload(self, name: str) -> None:
        """重载单个插件：重扫清单，再按启用状态重新加载。router 变更仍需重启。"""
        await self.unload(name)
        self.scan()
        if name in self.enabled:
            await self._load_one(name)

    async def set_enabled(self, name: str, enabled: bool) -> None:
        if enabled and name not in self.enabled:
            self.enabled.append(name)
        elif not enabled and name in self.enabled:
            self.enabled.remove(name)
        # 只动这一个插件：别的插件和它们的后台任务不受影响
        await self.unload(name)
        self.scan()
        if enabled:
            await self._load_one(name)

    def update_settings(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        manifest = self.manifests.get(name) or read_manifest(self.root_of(name), name)
        defaults = defaults_from(manifest.config)
        merged = {**defaults, **self.settings_of(name, defaults), **values}
        save_settings(self.settings_path(name), merged)
        for extension in self.extensions:
            if extension.ctx and extension.ctx.name == name:
                extension.ctx.settings = dict(merged)
        return merged

    def find_command(self, name: str) -> tuple[str, Any] | None:
        return self.commands.get(name)

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

    async def collect_user_suffix(self, ctx: HookContext) -> str:
        parts: list[str] = []
        for extension in self.extensions:
            try:
                text = await extension.user_suffix(ctx)
            except Exception as exc:  # noqa: BLE001
                log.exception("插件 %s user_suffix 异常", extension.name)
                self._mark_error(extension.name, exc)
                continue
            if text and text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)

    async def after_reply(self, ctx: HookContext, text: str) -> str:
        for extension in self.extensions:
            try:
                text = await extension.after_reply(ctx, text)
            except Exception as exc:  # noqa: BLE001
                log.exception("插件 %s after_reply 异常", extension.name)
                self._mark_error(extension.name, exc)
        return text






