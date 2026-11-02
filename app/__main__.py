"""python -m app 启动入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .config import CONFIG_PATH, load_config
from .main import build_app, setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="qq-bot-lite", description="OneBot 11 + 单次 LLM 请求的 QQ 机器人")
    parser.add_argument("-c", "--config", type=Path, default=CONFIG_PATH, help="配置文件路径")
    parser.add_argument("--host", help="覆盖监听地址")
    parser.add_argument("--port", type=int, help="覆盖监听端口")
    parser.add_argument("--log-level", help="覆盖日志级别 debug/info/warning/error")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.host:
        cfg.server.host = args.host
    if args.port:
        cfg.server.port = args.port
    if args.log_level:
        cfg.server.log_level = args.log_level

    setup_logging(cfg.server.log_level)
    app = build_app(cfg)
    uvicorn.run(
        app,
        host=cfg.server.host,
        port=cfg.server.port,
        log_config=None,
        ws="websockets-sansio",
        access_log=False,
    )


if __name__ == "__main__":
    main()
