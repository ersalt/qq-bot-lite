"""FastAPI 应用装配。"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse

from . import __version__
from .config import CONFIG_PATH, ROOT, Config, load_config
from .console import ConsoleFormatter, enable_ansi, print_banner, set_title, start_console_watchdog
from .context.store import MessageStore
from .core.pipeline import ChatService
from .llm.client import LLMClient
from .logfiles import LOG_KEEP, new_session_log, prune_session_logs, write_latest
from .media.images import ImageLoader
from .onebot.members import MemberDirectory
from .onebot.server import OneBotServer
from .persona import Persona
from .pluginkit.registry import ExtensionRegistry
from .reply.sender import ReplySender
from .web.docs import mount_docs
from .web.model_routes import mount_model_api
from .web.persona_routes import mount_persona_api
from .web.plugin_routes import mount_plugin_api, mount_plugin_routers
from .web.routes import mount_web
from .web.settings import save_to_toml

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
    now = _dt.datetime.now()
    try:
        log_path = new_session_log(log_dir, now=now)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
        )
        root.addHandler(file_handler)
        removed = prune_session_logs(log_dir)
        write_latest(log_dir, log_path)
        log.info("===== qq-bot-lite v%s 启动 %s =====", __version__, now.strftime("%Y-%m-%d %H:%M:%S"))
        log.info("日志文件: %s", log_path)
        if removed:
            log.info("清理旧日志: %d 个（保留最近 %d 次启动）", len(removed), LOG_KEEP)
    except OSError as exc:
        log.error("日志文件建不出来，本次只输出到控制台: %s", exc)

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


def build_app(
    cfg: Config,
    db_path: Path | None = None,
    config_path: Path | None = None,
    plugin_data_dir: Path | None = None,
    plugin_dir: Path | None = None,
    persona_dir: Path | None = None,
) -> FastAPI:
    store = MessageStore(db_path or (ROOT / "data" / "app.db"))
    persona = Persona(cfg)
    llm = LLMClient(cfg.llm)
    images = ImageLoader(cfg.images)
    members = MemberDirectory()
    sender = ReplySender(cfg, members)
    extensions = ExtensionRegistry(
        cfg.extensions.enabled,
        data_dir=plugin_data_dir,
        plugin_dir=plugin_dir,
        extra_dirs=[Path(item) for item in cfg.extensions.dirs if str(item).strip()],
    )

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
        members=members,
        get_connection=get_connection,
        get_self_id=get_self_id,
    )
    # 插件拿到的是这几个运行时对象，别的都不给。
    extensions.services = {"store": store, "service": service, "cfg": cfg, "llm": llm}
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
        mount_plugin_routers(app, extensions)
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
            await extensions.shutdown()
            await llm.aclose()
            await images.aclose()
            store.close()
            log.info("%s", service.stats_line(connected=False), extra={"tag": "stat"})
            log.info("===== 退出 %s =====", _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    app = FastAPI(title="qq-bot-lite", version=__version__, lifespan=lifespan)
    app.state.cfg = cfg
    app.state.store = store
    app.state.server = server
    app.state.plugin_registry = extensions

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
                "counters": {
                    "received": service.messages_seen,
                    "replied": service.replies_sent,
                    "first_ms": round(service.average_first_ms, 0),
                },
            }
        )

    @app.post("/api/shutdown")
    async def shutdown(request: Request) -> JSONResponse:
        """本机调用：让进程体面退出（exit 0），黑窗才会自己关掉。

        强行 taskkill 的话 start.cmd 会看到非 0 退出码，停在 “按任意键” 上，
        重启几次就攒一堆空窗口。
        """
        host = (request.client.host if request.client else "") or ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(status_code=403, detail="只允许本机调用")
        log.info("收到本机停机请求，准备退出", extra={"tag": "sys"})

        async def _stop() -> None:
            await asyncio.sleep(0.2)
            signal.raise_signal(signal.SIGINT)

        asyncio.create_task(_stop())
        return JSONResponse({"ok": True})

    def save_enabled(names: list[str]) -> None:
        path = config_path or CONFIG_PATH
        try:
            save_to_toml(path, {"extensions.enabled": names})
        except OSError:
            log.exception("写回 [extensions] enabled 失败")
        cfg.extensions.enabled = list(names)

    mount_plugin_api(app, cfg, extensions, save_enabled)
    mount_persona_api(app, cfg, persona, config_path or CONFIG_PATH, root=persona_dir)
    mount_model_api(app, cfg, llm, config_path or CONFIG_PATH)
    mount_web(app, cfg, persona, config_path or CONFIG_PATH, on_llm_change=llm.reload)
    mount_docs(app)

    return app


def create_app(config_path: Path | None = None) -> FastAPI:
    cfg = load_config(config_path)
    return build_app(cfg, config_path=config_path)














