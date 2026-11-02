"""插件式扩展点。

第一版没有群语汇，但留了三个钩子，第二阶段把 slang 做成 app/extensions/slang.py
在 config.toml 的 [extensions] enabled 里加 "slang" 就能挂上：

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
from dataclasses import dataclass
from typing import Any

from ..onebot.events import MessageEvent

log = logging.getLogger(__name__)


@dataclass(slots=True)
class HookContext:
    event: MessageEvent
    session_key: str
    is_group: bool
    history: list[dict[str, Any]]


class Extension:
    """扩展基类，全部钩子可选。"""

    name: str = "extension"

    async def on_message(self, ctx: HookContext) -> None:  # noqa: D401 - 钩子
        return None

    async def system_prompt(self, ctx: HookContext) -> str | None:
        return None

    async def after_reply(self, ctx: HookContext, text: str) -> str:
        return text


class ExtensionRegistry:
    def __init__(self, names: list[str]):
        self.names = list(names)
        self.extensions: list[Extension] = []

    async def load(self) -> None:
        for name in self.names:
            try:
                module = importlib.import_module(f"app.extensions.{name}")
            except Exception:  # noqa: BLE001
                log.exception("扩展加载失败: %s", name)
                continue
            try:
                extension = await _instantiate(module)
            except Exception:  # noqa: BLE001
                log.exception("扩展初始化失败: %s", name)
                continue
            if extension is None:
                log.warning("扩展 %s 没有提供 setup() 或 Extension 子类，已跳过", name)
                continue
            self.extensions.append(extension)
            log.info("扩展已启用: %s", getattr(extension, "name", name))

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


async def _instantiate(module: Any) -> Extension | None:
    factory = getattr(module, "setup", None)
    if factory is not None:
        result = factory()
        if inspect.isawaitable(result):
            result = await result
        return result
    candidate = getattr(module, "Extension", None)
    if isinstance(candidate, type) and candidate is not Extension:
        return candidate()
    return None
