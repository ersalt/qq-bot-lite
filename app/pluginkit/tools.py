"""插件工具（model-facing tools）的公共类型。

工具和 COMMANDS 是两套东西：
- COMMANDS 是用户输入 `/xxx` 触发；
- Tool 是模型通过 OpenAI 兼容的 tool_calls 触发。

插件在 Extension 上定义 `TOOLS`（或重写 `tools()`）即可注册；主程序只负责把 schema
交给模型、执行 tool_calls、把结果回填给模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .manifest import PluginContext

# 工具名会出现在 prompt 和日志里，限制成安全、稳定的标识符。
TOOL_NAME_LIMIT = 64


@dataclass(slots=True)
class ToolContext:
    """一次工具调用的上下文。"""

    plugin: str
    session_key: str
    is_group: bool
    is_admin: bool
    trigger: str = "chat"  # chat / autonomy
    data_dir: Path | None = None
    log: Any = None


@dataclass(slots=True)
class Tool:
    """一个 model-facing 工具。

    handler 收 (ToolContext, dict)，返回可 JSON 序列化的结果；异常由 ToolRunner 转成
    错误结果，不打断聊天主链路。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]
    read_only: bool = True

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
                or {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }


@dataclass(slots=True)
class ToolEntry:
    """已注册工具 + 它所属的插件上下文。"""

    tool: Tool
    plugin: str
    plugin_ctx: PluginContext
