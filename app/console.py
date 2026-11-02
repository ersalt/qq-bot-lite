"""控制台体验：彩色日志、启动横幅、窗口标题。

目标是像 SnowLuma 那样有个能看的黑窗：消息进出、回复耗时、错误一目了然。
输出重定向到文件或被管道捕获时自动退回纯文本。
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time

RESET = "\x1b[0m"
DIM = "\x1b[90m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
MAGENTA = "\x1b[35m"
BOLD = "\x1b[1m"

_TAG_COLORS = {
    "in": CYAN,
    "out": GREEN,
    "conn": MAGENTA,
    "stat": DIM,
    "sys": DIM,
    "warn": YELLOW,
}
_LEVEL_COLORS = {
    logging.DEBUG: DIM,
    logging.INFO: "",
    logging.WARNING: YELLOW,
    logging.ERROR: RED,
    logging.CRITICAL: RED + BOLD,
}


def enable_ansi() -> bool:
    """Windows 控制台默认不解析 ANSI，需要打开 VT 处理。"""
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001
        return False


def set_title(title: str) -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:  # noqa: BLE001
        pass


def start_console_watchdog() -> None:
    """关掉控制台窗口时保证进程跟着退出，别留下占着 8790 的孤儿进程。

    Windows 正常会发 CTRL_CLOSE_EVENT 直接杀进程，这里是兜底：
    连续 6 秒检测不到控制台窗口就 exit。
    """
    if os.name != "nt":
        return
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    except Exception:  # noqa: BLE001
        return
    if not hwnd:
        return

    def watch() -> None:
        strikes = 0
        while True:
            time.sleep(2)
            try:
                alive = bool(ctypes.windll.user32.IsWindow(hwnd))
            except Exception:  # noqa: BLE001
                return
            if alive:
                strikes = 0
                continue
            strikes += 1
            if strikes >= 3:
                os._exit(0)

    threading.Thread(target=watch, name="console-watchdog", daemon=True).start()


class ConsoleFormatter(logging.Formatter):
    def __init__(self, color: bool):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"

        if not self.color:
            return f"{stamp}  {message}"

        tag = getattr(record, "tag", None)
        color = _TAG_COLORS.get(tag) if tag else None
        level_tag = ""
        if record.levelno >= logging.WARNING:
            level_tag = f"{_LEVEL_COLORS.get(record.levelno, '')}{record.levelname:<5}{RESET} "
        if color is None:
            color = _LEVEL_COLORS.get(record.levelno, "")
        return f"{DIM}{stamp}{RESET}  {level_tag}{color}{message}{RESET}"


def print_banner(cfg, version: str) -> None:
    """启动横幅：关键配置一眼看全。"""
    lines = [
        f"{BOLD}qq-bot-lite{RESET} {DIM}v{version}  pid {os.getpid()}{RESET}",
        f"{DIM}反向 WS{RESET}   ws://{cfg.server.host}:{cfg.server.port}{cfg.onebot.path}",
        f"{DIM}模型{RESET}      {cfg.llm.model} @ {cfg.llm.base_url}  (stream={str(cfg.llm.stream).lower()})",
        f"{DIM}上下文{RESET}    最近 {cfg.context.max_messages} 条 / 约 {cfg.context.max_tokens} tokens",
        f"{DIM}人设{RESET}      {cfg.persona.file}",
        f"{DIM}回复{RESET}      引用={cfg.reply.quote} @={cfg.reply.at_sender} "
        f"分段={cfg.reply.segments}(≤{cfg.reply.max_segments})",
        f"{DIM}图片{RESET}      {cfg.images.enabled} 最长边 {cfg.images.max_side}px",
        f"{DIM}扩展{RESET}      {', '.join(cfg.extensions.enabled) or '无'}",
    ]
    width = 58
    print()
    print(f"{DIM}{'─' * width}{RESET}")
    for line in lines:
        print(f"  {line}")
    print(f"{DIM}{'─' * width}{RESET}")
    print(f"{DIM}  状态页 http://{cfg.server.host}:{cfg.server.port}/    Ctrl+C 停止{RESET}")
    print()
