"""qq-bot-lite —— 量身定制的 QQ 机器人（OneBot 11 反向 WS + 单次 LLM 请求）。"""

from __future__ import annotations

from pathlib import Path

# 版本号唯一来源：项目根目录的 VERSION 文件。
_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"

try:
    __version__ = _VERSION_FILE.read_text(encoding="utf-8").strip()
except OSError:  # 打包/裁剪后读不到时兜底，不让它影响启动
    __version__ = "0.0.0"
