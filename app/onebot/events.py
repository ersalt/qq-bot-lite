"""OneBot 11 事件归一化。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .protocol import ParsedContent, parse_content


@dataclass(slots=True)
class MessageEvent:
    self_id: int
    message_type: str  # group / private
    sub_type: str
    message_id: str | None
    user_id: int
    group_id: int | None
    content: ParsedContent
    sender_name: str
    timestamp: float
    raw: dict[str, Any]
    # 日志用：群名要异步查 get_group_info，查不到就 None
    group_name: str | None = None
    sender_card: str | None = None
    sender_nickname: str | None = None

    @property
    def is_group(self) -> bool:
        return self.message_type == "group"

    @property
    def session_key(self) -> str:
        if self.is_group:
            return f"group:{self.group_id}"
        return f"private:{self.user_id}"

    @property
    def target_label(self) -> str:
        """群「群名」(群号) / 私聊——日志里一眼看出在哪。"""
        if self.is_group:
            return f"群「{self.group_name or '未知群名'}」({self.group_id})"
        return "私聊"

    @property
    def sender_label(self) -> str:
        """昵称(QQ号)。"""
        return f"{self.sender_name}({self.user_id})"

    @property
    def reply_id(self) -> str | None:
        return self.content.reply_to

    @property
    def at_targets(self) -> list[str]:
        return self.content.at_targets

    def addressed_to_me(self, self_id: int) -> bool:
        return str(self_id) in self.content.at_targets


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_event(payload: dict[str, Any]) -> MessageEvent | None:
    """只处理实时收到的 message 事件；其余（notice/request/meta）忽略。"""
    if payload.get("post_type") != "message":
        return None
    if payload.get("message_type") not in {"group", "private"}:
        return None

    self_id = _to_int(payload.get("self_id")) or 0
    user_id = _to_int(payload.get("user_id"))
    if user_id is None:
        return None

    group_id = _to_int(payload.get("group_id"))
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    sender_card = str(sender.get("card") or "") or None
    sender_nickname = str(sender.get("nickname") or "") or None
    sender_name = str(sender_card or sender_nickname or user_id)

    message_id = payload.get("message_id")
    return MessageEvent(
        self_id=self_id,
        message_type=str(payload.get("message_type")),
        sub_type=str(payload.get("sub_type") or ""),
        message_id=None if message_id is None else str(message_id),
        user_id=user_id,
        group_id=group_id,
        content=parse_content(payload.get("message")),
        sender_name=sender_name,
        timestamp=float(payload.get("time") or time.time()),
        raw=payload,
        sender_card=sender_card,
        sender_nickname=sender_nickname,
    )
