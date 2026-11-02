"""SQLite 会话存储。"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session     TEXT    NOT NULL,
    role        TEXT    NOT NULL,
    speaker     TEXT,
    user_id     INTEGER,
    message_id  TEXT,
    content     TEXT    NOT NULL DEFAULT '',
    has_image   INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session, id);

CREATE TABLE IF NOT EXISTS sessions (
    key         TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    target_id   INTEGER NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sent_messages (
    session     TEXT NOT NULL,
    message_id  TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sent_lookup ON sent_messages(session, message_id);
"""


@dataclass(slots=True)
class StoredMessage:
    id: int
    session: str
    role: str
    speaker: str | None
    user_id: int | None
    content: str
    has_image: bool
    created_at: float


class MessageStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- 写入 ----------

    def add(
        self,
        session: str,
        role: str,
        content: str,
        *,
        speaker: str | None = None,
        user_id: int | None = None,
        message_id: str | None = None,
        has_image: bool = False,
    ) -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (session, role, speaker, user_id, message_id, content, has_image, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (session, role, speaker, user_id, message_id, content, int(has_image), now),
            )
            kind, _, target = session.partition(":")
            self._conn.execute(
                "INSERT INTO sessions (key, kind, target_id, updated_at) VALUES (?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET updated_at=excluded.updated_at",
                (session, kind, int(target) if target.isdigit() else 0, now),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def seen(self, session: str, message_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM messages WHERE session=? AND message_id=? LIMIT 1", (session, message_id)
            ).fetchone()
        return row is not None

    def mark_sent(self, session: str, message_ids: list[str]) -> None:
        """记住自己发出去的消息 id，用来识别“引用回复了机器人”。"""
        rows = [(session, str(mid), time.time()) for mid in message_ids if mid]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO sent_messages (session, message_id, created_at) VALUES (?,?,?)", rows
            )
            self._conn.commit()

    def is_sent(self, session: str, message_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM sent_messages WHERE session=? AND message_id=? LIMIT 1", (session, str(message_id))
            ).fetchone()
        return row is not None

    def prune(self, session: str, keep: int) -> int:
        if keep <= 0:
            return 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM messages WHERE session=? AND id NOT IN"
                " (SELECT id FROM messages WHERE session=? ORDER BY id DESC LIMIT ?)",
                (session, session, keep),
            )
            self._conn.commit()
            return cur.rowcount

    # ---------- 读取 ----------

    def recent(self, session: str, limit: int) -> list[StoredMessage]:
        if limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session=? ORDER BY id DESC LIMIT ?", (session, limit)
            ).fetchall()
        return [_row_to_message(row) for row in reversed(rows)]

    def count(self, session: str | None = None) -> int:
        with self._lock:
            if session is None:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
            else:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM messages WHERE session=?", (session,)).fetchone()
        return int(row["n"])

    def stats(self) -> dict[str, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
            sessions = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        return {"messages": int(total), "sessions": int(sessions)}


def _row_to_message(row: sqlite3.Row) -> StoredMessage:
    return StoredMessage(
        id=int(row["id"]),
        session=str(row["session"]),
        role=str(row["role"]),
        speaker=row["speaker"],
        user_id=row["user_id"],
        content=str(row["content"] or ""),
        has_image=bool(row["has_image"]),
        created_at=float(row["created_at"]),
    )
