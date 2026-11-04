"""SQLite 会话存储。"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session     TEXT    NOT NULL,
    role        TEXT    NOT NULL,
    speaker     TEXT,
    user_id     INTEGER,
    message_id  TEXT,
    content     TEXT    NOT NULL DEFAULT '',
    images      TEXT    NOT NULL DEFAULT '',
    has_image   INTEGER NOT NULL DEFAULT 0,
    reply_to    INTEGER,
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
    # 这张消息带的图，存的是 data/images/ 下的本地副本路径
    images: list[str] = field(default_factory=list)
    # assistant 消息对应的触发 user 消息 id；老数据为 None
    reply_to: int | None = None


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
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """老库补列：CREATE TABLE IF NOT EXISTS 不会给已有表加字段。"""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(messages)")}
        if "images" not in columns:
            self._conn.execute("ALTER TABLE messages ADD COLUMN images TEXT NOT NULL DEFAULT ''")
            log.info("数据库补上 messages.images 列")
        if "reply_to" not in columns:
            self._conn.execute("ALTER TABLE messages ADD COLUMN reply_to INTEGER")
            log.info("数据库补上 messages.reply_to 列")

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
        images: list[str] | None = None,
        reply_to: int | None = None,
    ) -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (session, role, speaker, user_id, message_id, content, has_image, "
                "images, reply_to, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    session,
                    role,
                    speaker,
                    user_id,
                    message_id,
                    content,
                    int(has_image),
                    json.dumps(images or [], ensure_ascii=False),
                    reply_to,
                    now,
                ),
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

    def after(self, session: str, after_id: int, limit: int) -> list[StoredMessage]:
        """取 id > after_id 的最早 N 条，用于“锚点窗口”：窗口只在锚点推进时才变。"""
        if limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session=? AND id>? ORDER BY id ASC LIMIT ?",
                (session, after_id, limit),
            ).fetchall()
        return [_row_to_message(row) for row in rows]

    def latest_after(self, session: str, after_id: int, limit: int) -> list[StoredMessage]:
        """取 id > after_id 的**最新** N 条，旧→新返回。

        `after()` 是最早优先；历史超过 limit 时它会停在旧消息上，最新的几条根本进不了
        窗口。锚点窗口必须用这个，否则模型永远慢几十条（实测落后 431 条）。
        """
        if limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session=? AND id>? ORDER BY id DESC LIMIT ?",
                (session, after_id, limit),
            ).fetchall()
        return [_row_to_message(row) for row in reversed(rows)]

    def count(self, session: str | None = None) -> int:
        with self._lock:
            if session is None:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
            else:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM messages WHERE session=?", (session,)).fetchone()
        return int(row["n"])

    def last_role_id(self, session: str, role: str = "assistant") -> int:
        """该会话最后一条指定角色的消息 id；没有返回 0。用于圈 ambient 起点。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM messages WHERE session=? AND role=? ORDER BY id DESC LIMIT 1",
                (session, role),
            ).fetchone()
        return int(row["id"]) if row else 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
            sessions = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        return {"messages": int(total), "sessions": int(sessions)}


def _row_to_message(row: sqlite3.Row) -> StoredMessage:
    raw_images = row["images"] if "images" in row.keys() else ""
    try:
        images = [str(item) for item in json.loads(raw_images or "[]")]
    except (ValueError, TypeError):
        images = []
    return StoredMessage(
        id=int(row["id"]),
        session=str(row["session"]),
        role=str(row["role"]),
        speaker=row["speaker"],
        user_id=row["user_id"],
        content=str(row["content"] or ""),
        has_image=bool(row["has_image"]),
        created_at=float(row["created_at"]),
        images=images,
        reply_to=row["reply_to"] if "reply_to" in row.keys() else None,
    )


