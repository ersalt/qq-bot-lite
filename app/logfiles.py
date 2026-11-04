"""每次启动一个日志文件，保留历史，方便按启动批次排查。"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

LOG_PREFIX = "app-"
LOG_SUFFIX = ".log"
LATEST_NAME = "latest.txt"
# 保留最近 N 次启动；每次启动一个文件，不按大小滚动，避免一份日志横跨很多次运行。
LOG_KEEP = 30
_SESSION_RE = re.compile(r"^app-(\d{8}-\d{6})(?:-(\d+))?\.log$")


def new_session_log(log_dir: Path, *, now: _dt.datetime | None = None) -> Path:
    """创建本次启动的日志文件，返回路径；同一秒内重名时加 -1/-2。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or _dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    candidate = log_dir / f"{LOG_PREFIX}{stamp}{LOG_SUFFIX}"
    index = 1
    while candidate.exists():
        candidate = log_dir / f"{LOG_PREFIX}{stamp}-{index}{LOG_SUFFIX}"
        index += 1
    candidate.touch()
    return candidate


def _session_key(path: Path) -> tuple[str, int]:
    match = _SESSION_RE.match(path.name)
    if not match:
        return ("", 0)
    return (match.group(1), int(match.group(2) or 0))


def prune_session_logs(log_dir: Path, keep: int = LOG_KEEP) -> list[Path]:
    """只删匹配 app-YYYYMMDD-HHMMSS[-n].log 的旧文件；不碰 legacy app.log。"""
    if keep <= 0:
        return []
    files = [path for path in log_dir.glob(f"{LOG_PREFIX}*{LOG_SUFFIX}") if _SESSION_RE.match(path.name)]
    files.sort(key=_session_key)
    stale = files[:-keep]
    removed: list[Path] = []
    for path in stale:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    return removed


def write_latest(log_dir: Path, path: Path) -> None:
    """写一个 latest.txt 指向当前日志，省得每次去目录里找最新文件。"""
    try:
        (log_dir / LATEST_NAME).write_text(path.name + "\n", encoding="utf-8")
    except OSError:
        pass
