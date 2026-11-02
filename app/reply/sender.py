"""把文本发回 QQ：引用、真 @、分段、去重。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from ..config import Config
from ..onebot.client import OneBotActionError, OneBotConnection
from ..onebot.events import MessageEvent
from ..onebot.protocol import at_segment, reply_segment, text_segment

log = logging.getLogger(__name__)

_CQ_RE = re.compile(r"\[CQ:[^\]]*\]")


@dataclass(slots=True)
class SendResult:
    """一次回复实际发生了什么，日志直接拿去用。"""

    sent: int = 0
    reply_to: str | None = None
    at_targets: list[str] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)


def _message_id(data: Any) -> str | None:
    if isinstance(data, dict):
        mid = data.get("message_id")
        if mid is not None:
            return str(mid)
    return None


def clean_text(text: str, bot_name: str) -> str:
    """模型偶尔会自己写 @ 或 CQ 码，这里兜底清掉，避免发出半截语法。"""
    text = _CQ_RE.sub("", text)
    text = text.replace("\u200b", "")
    if bot_name:
        text = re.sub(rf"^\s*{re.escape(bot_name)}\s*[:：]\s*", "", text)
    return text.strip()


class ReplySender:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._sent: dict[tuple[str, str], float] = {}

    async def deliver(
        self,
        conn: OneBotConnection,
        event: MessageEvent,
        pieces: AsyncIterator[str],
        on_sent: Callable[[str], None] | None = None,
    ) -> "SendResult":
        result = SendResult()
        first = True
        interval = self.cfg.reply.segment_interval if self.cfg.reply.segments else 0.0
        async for piece in pieces:
            text = clean_text(piece, self.cfg.persona.nickname)
            if not text:
                continue
            if self._is_duplicate(event.session_key, text):
                log.info("跳过重复内容: %.40s", text, extra={"tag": "sys"})
                continue
            message_id, reply_used = await self._send_one(conn, event, text, with_meta=first)
            if first and reply_used:
                result.reply_to = reply_used
            if first and self.cfg.reply.at_sender and event.is_group:
                result.at_targets.append(str(event.user_id))
            if message_id:
                result.message_ids.append(message_id)
                if on_sent:
                    on_sent(message_id)
            # 发出去的内容按“普通消息”格式打一条，和入站同构，方便对着看。
            log.info(
                "→ %s | %s(%s) | msg=%s | 回复 %s | %s",
                event.target_label,
                self.cfg.persona.nickname,
                event.self_id,
                message_id or "-",
                event.sender_label,
                text,
                extra={"tag": "out"},
            )
            first = False
            result.sent += 1
            if interval > 0:
                await asyncio.sleep(interval)
        return result

    async def send_plain(self, conn: OneBotConnection, event: MessageEvent, text: str) -> None:
        await self._send_one(conn, event, clean_text(text, self.cfg.persona.nickname), with_meta=True)

    # ---------- 内部 ----------

    async def _send_one(
        self, conn: OneBotConnection, event: MessageEvent, text: str, *, with_meta: bool
    ) -> tuple[str | None, str | None]:
        """返回 (message_id, 实际生效的引用 id)。"""
        segments: list[dict] = []
        reply_used: str | None = None
        if with_meta and self.cfg.reply.quote and event.message_id:
            segments.append(reply_segment(event.message_id))
            reply_used = str(event.message_id)
        if with_meta and self.cfg.reply.at_sender and event.is_group:
            segments.append(at_segment(event.user_id))
            segments.append(text_segment(" "))
        segments.append(text_segment(text))

        try:
            return _message_id(await self._call(conn, event, segments)), reply_used
        except OneBotActionError as exc:
            # 引用目标可能已过期，去掉 reply 段再试一次，别让整条回复丢掉。
            if reply_used is not None:
                log.warning("带引用发送失败，改为不带引用重试: %s", exc, extra={"tag": "warn"})
                return _message_id(await self._call(conn, event, segments[1:])), None
            log.error(
                "发送失败 group=%s user=%s msg=%s: %s",
                event.group_id,
                event.user_id,
                event.message_id,
                exc,
            )
            raise

    async def _call(self, conn: OneBotConnection, event: MessageEvent, segments: list[dict]) -> Any:
        timeout = self.cfg.onebot.action_timeout
        if event.is_group and event.group_id is not None:
            return await conn.send_group_msg(event.group_id, segments, timeout=timeout)
        return await conn.send_private_msg(event.user_id, segments, timeout=timeout)

    def _is_duplicate(self, session: str, text: str) -> bool:
        window = self.cfg.reply.dedupe_window
        if window <= 0:
            return False
        key = (session, hashlib.sha1(text.encode("utf-8")).hexdigest())
        now = time.time()
        last = self._sent.get(key)
        if last is not None and now - last < window:
            return True
        self._sent[key] = now
        if len(self._sent) > 512:
            cutoff = now - max(window, 60.0)
            self._sent = {k: v for k, v in self._sent.items() if v >= cutoff}
        return False
