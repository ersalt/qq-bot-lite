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
from ..onebot.members import MemberDirectory
from ..onebot.protocol import at_segment, reply_segment, text_segment

log = logging.getLogger(__name__)

_CQ_RE = re.compile(r"\[CQ:[^\]]*\]")
# 模型写的提及：统一用 "at 昵称"，兼容旧写法 "@昵称"。
# 尾巴上的 "(QQ号)" 必须一起吃掉：历史里存的就是 "at 名字(123456)"，模型会照着学，
# 只匹配名字的话括号里的号会当正文留下来，发出去就成了 "at 贰叔(10001) (10001)"。
_MENTION_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:at\s+|@)([\w\u4e00-\u9fff·]{1,20})(?:[(（](\d{3,12})[)）])?"
)


@dataclass(slots=True)
class SendResult:
    """一次回复实际发生了什么，日志直接拿去用。"""

    sent: int = 0
    reply_to: str | None = None
    at_targets: list[str] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)
    # 实际发出去的文本（at 段已渲染成 at 名字(QQ)），用于写回上下文
    texts: list[str] = field(default_factory=list)


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
    def __init__(self, cfg: Config, members: MemberDirectory | None = None):
        self.cfg = cfg
        self._sent: dict[tuple[str, str], float] = {}
        self.members = members or MemberDirectory()

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
            message_id, reply_used, segments = await self._send_one(conn, event, text, with_meta=first)
            if first and reply_used:
                result.reply_to = reply_used
            if first and self.cfg.reply.at_sender and event.is_group:
                result.at_targets.append(str(event.user_id))
            if message_id:
                result.message_ids.append(message_id)
                if on_sent:
                    on_sent(message_id)
            # 打的是“实际发出去的段”，不是模型原始输出：at 段要显示成 at 名字(QQ)
            # 否则日志里会冒出模型写的 @，和真实发送内容对不上。
            sent_text = await self._render_sent(conn, event, segments)
            result.texts.append(sent_text)
            log.info(
                "→ %s | %s(%s) | msg=%s | 回复 %s | %s",
                event.target_label,
                event.self_name or self.cfg.persona.nickname,
                event.self_id,
                message_id or "-",
                event.sender_label,
                sent_text,
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
    ) -> tuple[str | None, str | None, list[dict]]:
        """返回 (message_id, 实际生效的引用 id, 实际发送的段)。"""
        segments: list[dict] = []
        reply_used: str | None = None
        if with_meta and self.cfg.reply.quote and event.message_id:
            segments.append(reply_segment(event.message_id))
            reply_used = str(event.message_id)
        if with_meta and self.cfg.reply.at_sender and event.is_group:
            segments.append(at_segment(event.user_id))
            segments.append(text_segment(" "))
        segments.extend(await self._text_segments(conn, event, text))

        try:
            return _message_id(await self._call(conn, event, segments)), reply_used, segments
        except OneBotActionError as exc:
            # 引用目标可能已过期，去掉 reply 段再试一次，别让整条回复丢掉。
            if reply_used is not None:
                log.warning("带引用发送失败，改为不带引用重试: %s", exc, extra={"tag": "warn"})
                retry = segments[1:]
                return _message_id(await self._call(conn, event, retry)), None, retry
            log.error(
                "发送失败 group=%s user=%s msg=%s: %s",
                event.group_id,
                event.user_id,
                event.message_id,
                exc,
            )
            raise

    async def _render_sent(self, conn: OneBotConnection, event: MessageEvent, segments: list[dict]) -> str:
        """把要发的段渲染成日志文本：at 段显示 at 名字(QQ)，text 段原样。"""
        out: list[str] = []
        for segment in segments:
            seg_type = segment.get("type")
            data = segment.get("data") or {}
            if seg_type == "text":
                out.append(str(data.get("text", "")))
            elif seg_type == "at":
                qq = str(data.get("qq", ""))
                name = None
                if event.group_id is not None and qq.isdigit():
                    try:
                        name = await self.members.name_of(conn, event.group_id, int(qq))
                    except Exception:  # noqa: BLE001 - 只是日志，别影响发送
                        name = None
                out.append(f"at {name}({qq})" if name else f"at {qq}")
        return "".join(out).strip()

    async def _text_segments(self, conn: OneBotConnection, event: MessageEvent, text: str) -> list[dict]:
        """把模型写的 at 昵称 换成真正的 at 段；解析不到就原样当文本。"""
        if not self.cfg.reply.resolve_mentions or not event.is_group or event.group_id is None:
            return [text_segment(text)]

        segments: list[dict] = []
        pos = 0
        found = 0
        for match in _MENTION_RE.finditer(text):
            if found >= self.cfg.reply.max_mentions:
                break
            explicit = match.group(2)
            if explicit:
                # 模型已经自己写了 QQ 号，直接认，不用查名单
                user_id: int | None = int(explicit)
            else:
                user_id = await self.members.resolve(conn, event.group_id, match.group(1))
            if user_id is None or user_id == event.self_id:
                continue
            if match.start() > pos:
                segments.append(text_segment(text[pos : match.start()]))
            segments.append(at_segment(user_id))
            segments.append(text_segment(" "))
            pos = match.end()
            found += 1

        if found == 0:
            return [text_segment(text)]
        if pos < len(text):
            segments.append(text_segment(text[pos:]))
        log.info("已解析 %d 个 @ 目标", found, extra={"tag": "sys"})
        return segments
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
