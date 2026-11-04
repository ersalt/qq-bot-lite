"""每次启动一个日志文件的命名、保留和 latest 指针。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from app.logfiles import LATEST_NAME, new_session_log, prune_session_logs, write_latest


def test_new_session_log_is_unique_per_second(tmp_path: Path) -> None:
    now = dt.datetime(2026, 11, 4, 21, 15, 0)
    first = new_session_log(tmp_path, now=now)
    second = new_session_log(tmp_path, now=now)
    assert first.name == "app-20261104-211500.log"
    assert second.name == "app-20261104-211500-1.log"
    assert first.exists() and second.exists()


def test_prune_keeps_latest_sessions_and_leaves_legacy(tmp_path: Path) -> None:
    for minute in range(5):
        new_session_log(tmp_path, now=dt.datetime(2026, 11, 4, 21, minute, 0))
    legacy = tmp_path / "app.log"
    legacy.write_text("legacy\n", encoding="utf-8")
    other = tmp_path / "notes.txt"
    other.write_text("keep\n", encoding="utf-8")

    removed = prune_session_logs(tmp_path, keep=2)
    assert {path.name for path in removed} == {
        "app-20261104-210000.log",
        "app-20261104-210100.log",
        "app-20261104-210200.log",
    }
    remaining = sorted(
        path.name for path in tmp_path.glob("app-*.log") if path.name.startswith("app-2026")
    )
    assert remaining[-2:] == ["app-20261104-210300.log", "app-20261104-210400.log"]
    assert legacy.exists() and other.exists()


def test_write_latest_points_to_current_log(tmp_path: Path) -> None:
    path = new_session_log(tmp_path, now=dt.datetime(2026, 11, 4, 21, 15, 0))
    write_latest(tmp_path, path)
    assert (tmp_path / LATEST_NAME).read_text(encoding="utf-8") == path.name + "\n"
