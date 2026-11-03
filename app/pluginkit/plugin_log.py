"""插件日志：统一前缀，作者不用自己拼 `[标题]`。

控制台只打 message，所以前缀就是「这条日志是谁说的」；文件日志里 logger 名是
`plugin.<id>`，比 `qqbotlite_plugin_xxx.core.miner` 好认。

规范见 docs/plugin.md「日志」一节：面向用户的事件走 `ctx.log`，
排错细节可以用模块自己的 logger。
"""

from __future__ import annotations

import logging


class PluginLogger(logging.LoggerAdapter):
    """给每条日志加 `[插件标题] ` 前缀。"""

    def __init__(self, logger: logging.Logger, title: str):
        super().__init__(logger, {"title": title or logger.name})

    def process(self, msg, kwargs):  # noqa: D102 - LoggerAdapter 约定
        return f"[{self.extra['title']}] {msg}", kwargs


def make_plugin_logger(name: str, title: str) -> PluginLogger:
    return PluginLogger(logging.getLogger(f"plugin.{name}"), title or name)
