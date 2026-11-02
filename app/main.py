"""FastAPI 应用装配。"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse

from . import __version__
from .config import ROOT, Config, load_config
from .console import ConsoleFormatter, enable_ansi, print_banner, set_title, start_console_watchdog
from .context.store import MessageStore
from .core.pipeline import ChatService
from .extensions.base import ExtensionRegistry
from .llm.client import LLMClient
from .media.images import ImageLoader
from .onebot.server import OneBotServer
from .persona import Persona
from .reply.sender import ReplySender

log = logging.getLogger(__name__)


def _fix_windows_console() -> None:
    """Windows 控制台默认 GBK，中文日志会变乱码。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001 - 没有控制台（服务方式运行）时会失败
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def setup_logging(level: str = "info") -> bool:
    """控制台彩色 + 文件纯文本双通道。返回是否启用了彩色。"""
    _fix_windows_console()
    color = enable_ansi()

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(ConsoleFormatter(color))
    root.addHandler(stream)

    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    )
    root.addHandler(file_handler)

    for noisy in ("httpx", "httpcore", "uvicorn.error", "uvicorn.access", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return color


def _install_loop_error_filter() -> None:
    """Windows 上对端突然断开时，Proactor 会往事件循环里抛 ConnectionResetError。

    这类噪音跟业务无关，只降成 debug，别在控制台刷红字。
    """
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def handler(_: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        message = str(context.get("message", ""))
        if isinstance(exc, ConnectionResetError) or "10054" in message:
            log.debug("忽略连接重置: %s", message)
            return
        if previous is not None:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


def build_app(cfg: Config, db_path: Path | None = None) -> FastAPI:
    store = MessageStore(db_path or (ROOT / "data" / "app.db"))
    persona = Persona(cfg)
    llm = LLMClient(cfg.llm)
    images = ImageLoader(cfg.images)
    sender = ReplySender(cfg)
    extensions = ExtensionRegistry(cfg.extensions.enabled)

    server_holder: dict[str, OneBotServer] = {}

    def get_connection():
        server = server_holder.get("server")
        return server.connection if server else None

    def get_self_id() -> int | None:
        server = server_holder.get("server")
        return server.self_id if server else None

    service = ChatService(
        cfg,
        store=store,
        persona=persona,
        llm=llm,
        images=images,
        sender=sender,
        extensions=extensions,
        get_connection=get_connection,
        get_self_id=get_self_id,
    )
    server = OneBotServer(cfg.onebot, service.handle)
    server_holder["server"] = server

    async def _status_loop() -> None:
        interval = cfg.server.status_interval
        if interval <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            log.info(
                "%s",
                service.stats_line(connected=server.connection is not None),
                extra={"tag": "stat"},
            )

    async def _title_loop() -> None:
        """窗口标题常驻显示状态：就算不滚日志，也知道它活着、连着没有。"""
        while True:
            await asyncio.sleep(15)
            online = server.connection is not None
            state = "在线" if online else "离线"
            set_title(
                f"qq-bot-lite · {state} · 收 {service.messages_seen} 回 {service.replies_sent}"
                f" · :{cfg.server.port}"
            )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        _install_loop_error_filter()
        start_console_watchdog()
        set_title(f"qq-bot-lite · 启动中 · :{cfg.server.port}")
        print_banner(cfg, __version__)
        await extensions.load()
        log.info("等待 SnowLuma 接入……", extra={"tag": "conn"})
        status_task = asyncio.create_task(_status_loop())
        title_task = asyncio.create_task(_title_loop())
        try:
            yield
        finally:
            status_task.cancel()
            title_task.cancel()
            set_title(f"qq-bot-lite · 已停止 · :{cfg.server.port}")
            log.info("正在关闭……", extra={"tag": "sys"})
            await server.shutdown()
            await llm.aclose()
            await images.aclose()
            store.close()
            log.info("%s", service.stats_line(connected=False), extra={"tag": "stat"})

    app = FastAPI(title="qq-bot-lite", version=__version__, lifespan=lifespan)
    app.state.cfg = cfg
    app.state.store = store
    app.state.server = server

    @app.websocket(cfg.onebot.path)
    async def onebot_ws(websocket: WebSocket) -> None:  # noqa: ANN202
        await server.endpoint(websocket)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "connected": server.connection is not None, "self_id": server.self_id}

    @app.get("/api/status")
    async def api_status() -> JSONResponse:
        return JSONResponse(
            {
                "version": __version__,
                "connected": server.connection is not None,
                "self_id": server.self_id,
                "model": cfg.llm.model,
                "base_url": cfg.llm.base_url,
                "ws_path": cfg.onebot.path,
                "port": cfg.server.port,
                "extensions": cfg.extensions.enabled,
                "db": store.stats(),
            }
        )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        stats = store.stats()
        online = server.connection is not None
        return _STATUS_PAGE.format(
            version=__version__,
            connected="在线" if online else "未连接",
            conn_cls="ok" if online else "off",
            self_id=server.self_id or "-",
            model=cfg.llm.model,
            base_url=cfg.llm.base_url,
            ws=f"{cfg.server.host}:{cfg.server.port}{cfg.onebot.path}",
            messages=stats["messages"],
            sessions=stats["sessions"],
            context_messages=cfg.context.max_messages,
            context_tokens=cfg.context.max_tokens,
            extensions=", ".join(cfg.extensions.enabled) or "无",
        )

    return app


_STATUS_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>qq-bot-lite</title>
<style>
 body{{background:#111318;color:#d7dae0;font:14px/1.7 -apple-system,"Segoe UI",sans-serif;margin:0;padding:40px}}
 main{{max-width:720px;margin:0 auto}}
 h1{{font-size:18px;font-weight:600;margin:0 0 4px}}
 .sub{{color:#7d838f;margin-bottom:24px}}
 table{{border-collapse:collapse;width:100%}}
 td{{padding:8px 0;border-bottom:1px solid #22262e;vertical-align:top}}
 td:first-child{{color:#7d838f;width:180px}}
 code{{background:#1a1e26;padding:1px 6px;border-radius:4px;color:#9ecbff}}
 .ok{{color:#4ade80}} .off{{color:#f87171}}
</style></head><body><main>
<h1>qq-bot-lite <span class="sub">v{version}</span></h1>
<div class="sub">QQ ↔ SnowLuma ↔ 本项目 ↔ LM Studio</div>
<table>
 <tr><td>OneBot 连接</td><td class="{conn_cls}">{connected}（self_id: {self_id}）</td></tr>
 <tr><td>反向 WS 地址</td><td><code>ws://{ws}</code></td></tr>
 <tr><td>模型</td><td><code>{model}</code> @ {base_url}</td></tr>
 <tr><td>上下文</td><td>最近 {context_messages} 条 / 约 {context_tokens} tokens</td></tr>
 <tr><td>扩展</td><td>{extensions}</td></tr>
 <tr><td>数据库</td><td>{messages} 条消息 / {sessions} 个会话</td></tr>
</table>
<p class="sub">配置见 <code>config/config.toml</code>，改完重启生效。</p>
</main></body></html>"""


def create_app(config_path: Path | None = None) -> FastAPI:
    cfg = load_config(config_path)
    return build_app(cfg)
