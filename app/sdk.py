"""插件 SDK：插件该 import 的唯一入口。

**插件只准 import 这个模块**，不要直接引 `app.pluginkit.*` 或 `app.core.*`。
里面用到的名字属于 v1 契约（见 docs/plugin-api.md），内部怎么重构都不影响插件。

    from app.sdk import Extension, HookContext, Command, CommandContext

    class MyPlugin(Extension):
        name = "my_plugin"
        async def on_message(self, hook: HookContext) -> None: ...

    async def handle(cmd: CommandContext) -> str:
        return "收到：" + " ".join(cmd.args)

    COMMANDS = [Command("mycmd", handler=handle, help="示例命令", admin=True)]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .pluginkit.manifest import SUPPORTED_API, PluginContext
from .pluginkit.registry import Extension, HookContext

__all__ = [
    "API_VERSION",
    "Command",
    "CommandContext",
    "Extension",
    "HookContext",
    "PluginContext",
]

API_VERSION = SUPPORTED_API


@dataclass(slots=True)
class CommandContext:
    """一次命令调用的上下文。"""

    plugin: str
    name: str
    args: list[str]
    text: str  # 命令名之后的原文
    event: Any  # MessageEvent，字段见文档
    session_key: str
    is_group: bool
    is_admin: bool

    @property
    def sender_name(self) -> str:
        return getattr(self.event, "sender_name", "") or str(getattr(self.event, "user_id", ""))


@dataclass(slots=True)
class Command:
    """插件导出的 `COMMANDS` 里的一项。

    handler 收一个 CommandContext，返回要发出去的文本；返回空字符串/None 表示不回。
    """

    name: str
    handler: Callable[[CommandContext], Awaitable[str | None]]
    help: str = ""
    admin: bool = False
    aliases: list[str] = field(default_factory=list)
