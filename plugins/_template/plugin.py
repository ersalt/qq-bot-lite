"""插件模板：复制整个目录、改目录名，再改 plugin.toml 里的 title / version。

这个文件把「一个插件能用到的东西」都摆了一遍，用完删掉不需要的部分：

- setup(ctx)：必需，返回扩展实例
- on_start / on_stop：可选，起后台任务、收尾
- on_message / user_suffix / after_reply：可选，消息链路上的钩子
- router：可选，面板接口（挂在 /plugins/<id>/ 下）
- COMMANDS：可选，/命令
- ctx.log：日志自动带 `[插件模板]` 前缀，别自己拼

规范见 docs/plugin-spec.md。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.sdk import Command, CommandContext, Extension, HookContext, PluginContext

router = APIRouter()

# 运行期状态放内存没问题；要跨重启就写 ctx.data_dir 里的 SQLite / JSON
STATE = {"messages": 0, "started_at": 0.0}
_CONTEXT: PluginContext | None = None


@router.get("/data")
async def data() -> JSONResponse:
    """面板读这个接口。约定：统一返回 {ok, ...}，错误也带 message。"""
    return JSONResponse({"ok": True, **STATE})


class Template(Extension):
    name = "_template"

    # ---------- 生命周期 ----------

    async def on_start(self) -> None:
        STATE["started_at"] = asyncio.get_event_loop().time()
        self.ctx.log.info("已启动（data_dir=%s）", self.ctx.data_dir)
        self.ctx.spawn(self._tick())  # 退出时会被统一取消

    async def on_stop(self) -> None:
        self.ctx.log.info("收工")

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(60)
            # 后台活别在钩子里干，放这儿

    # ---------- 钩子 ----------

    async def on_message(self, ctx: HookContext) -> None:
        if not self.ctx.get("enabled", True):
            return
        STATE["messages"] += 1

    async def user_suffix(self, ctx: HookContext) -> str | None:
        """要给模型的补充内容，追加到这条用户消息末尾。

        别用 system_prompt 塞会变的东西：system 一变，模型的 KV 缓存就废了。
        """
        return None

    # ---------- 设置面板的兜底接口 ----------

    async def settings_fields(self, ctx: PluginContext, fields: list[dict]):
        """打开设置弹窗时能改写字段：补动态选项、按状态隐藏、甚至返回 []。

        返回 None 表示“按 plugin.toml 原样渲染”。模板里不动它。
        """
        return None


async def handle_tpl(cmd: CommandContext) -> str:
    return f"模板插件在跑，收到过 {STATE['messages']} 条消息"


def setup(ctx: PluginContext) -> Template:
    global _CONTEXT
    _CONTEXT = ctx
    plugin = Template()
    plugin.ctx = ctx
    return plugin


COMMANDS = [Command("tpl", handler=handle_tpl, help="模板：/tpl", admin=True)]
