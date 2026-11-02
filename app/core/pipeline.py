"""消息处理主流程：触发判断 → 上下文 → 单次模型请求 → 分段发送。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from ..config import Config
from ..context.store import MessageStore
from ..context.window import build_history
from ..extensions.base import ExtensionRegistry, HookContext
from ..llm.client import LLMClient, LLMError, ThinkingBudgetExceeded
from ..llm.prompt import build_messages, build_system_prompt
from ..llm.tokens import estimate_text_tokens
from ..media.images import ImageLoader
from ..onebot.client import OneBotConnection
from ..onebot.events import MessageEvent
from ..persona import Persona
from ..reply.segments import Segmenter
from ..reply.sender import ReplySender, clean_text
from .trigger import Decision, decide

log = logging.getLogger(__name__)


@dataclass(slots=True)
class _Timing:
    start: float = field(default_factory=time.perf_counter)
    first_piece: float | None = None

    def mark_first(self) -> None:
        if self.first_piece is None:
            self.first_piece = time.perf_counter()

    @property
    def first_ms(self) -> float | None:
        if self.first_piece is None:
            return None
        return (self.first_piece - self.start) * 1000

    @property
    def total_ms(self) -> float:
        return (time.perf_counter() - self.start) * 1000

    def summary(self) -> str:
        total = self.total_ms
        if self.first_piece is None:
            return f"total={total:.0f}ms"
        return f"first={self.first_ms:.0f}ms total={total:.0f}ms"


class ChatService:
    def __init__(
        self,
        cfg: Config,
        *,
        store: MessageStore,
        persona: Persona,
        llm: LLMClient,
        images: ImageLoader,
        sender: ReplySender,
        extensions: ExtensionRegistry,
        get_connection: Callable[[], OneBotConnection | None],
        get_self_id: Callable[[], int | None],
    ):
        self.cfg = cfg
        self.store = store
        self.persona = persona
        self.llm = llm
        self.images = images
        self.sender = sender
        self.extensions = extensions
        self.get_connection = get_connection
        self.get_self_id = get_self_id
        self._locks: dict[str, asyncio.Lock] = {}
        self._cooldown: dict[str, float] = {}
        self._since_prune: dict[str, int] = {}
        self.started_at = time.time()
        self.messages_seen = 0
        self.replies_sent = 0
        self._first_ms: list[float] = []
        self._group_names: dict[int, tuple[str, float]] = {}

    # ---------- 入口 ----------

    async def handle(self, event: MessageEvent) -> None:
        session = event.session_key
        if event.message_id and self.store.seen(session, event.message_id):
            log.debug("重复事件，跳过 message_id=%s", event.message_id)
            return

        decision = decide(
            self.cfg,
            event,
            self.get_self_id(),
            lambda mid: self.store.is_sent(session, mid),
        )

        group_label = await self._group_label(event)
        preview = event.content.text.strip()[:60] or ("[图片]" if event.content.images else "")
        self.messages_seen += 1
        if decision.should:
            log.info(
                "← %s | %s | msg=%s%s%s | %s",
                group_label,
                event.sender_label,
                event.message_id or "-",
                f" | 引用={event.reply_id}" if event.reply_id else "",
                f" | @={','.join(event.at_targets)}" if event.at_targets else "",
                preview,
                extra={"tag": "in"},
            )
        else:
            log.debug(
                "← %s | %s | msg=%s | %s（不回复：%s）",
                group_label,
                event.sender_label,
                event.message_id or "-",
                preview,
                decision.reason,
            )

        # 历史要在当前消息落库前取，避免把“当前这条”重复送进模型。
        history = self.store.recent(session, self.cfg.context.max_messages)
        self.store.add(
            session,
            "user",
            event.content.text,
            speaker=event.sender_name,
            user_id=event.user_id,
            message_id=event.message_id,
            has_image=bool(event.content.images),
        )
        self._maybe_prune(session)

        ctx = HookContext(event=event, session_key=session, is_group=event.is_group, history=[])
        try:
            await self.extensions.on_message(ctx)
        except Exception:  # noqa: BLE001 - 扩展问题不影响主流程
            log.exception("扩展 on_message 阶段异常")

        if not decision.should:
            log.debug("不回复 %s reason=%s text=%.40s", session, decision.reason, event.content.text)
            return

        if not self._cooldown_ok(session):
            log.info("冷却中，跳过 %s", session)
            return

        conn = self.get_connection()
        if conn is None:
            log.warning("当前没有 OneBot 连接，丢弃回复 session=%s", session)
            return

        async with self._lock_for(session):
            await self._reply(conn, event, history, ctx, decision)

    # ---------- 回复 ----------

    async def _reply(
        self,
        conn: OneBotConnection,
        event: MessageEvent,
        history_messages: list,
        ctx: HookContext,
        decision: Decision,
    ) -> None:
        timing = _Timing()
        history = build_history(history_messages, self.cfg.context, is_group=event.is_group)
        ctx.history = history

        content = await self.images.build_content(
            _prompt_text(event, quoted=decision.reason == "quote"), event.content.images
        )
        extra = await self.extensions.collect_system_prompt(ctx)
        system_prompt = build_system_prompt(
            self.cfg,
            self.persona.text,
            is_group=event.is_group,
            group_id=event.group_id,
            speaker=event.sender_name,
            extra_sections=extra,
        )
        messages = build_messages(system_prompt, history, content, self.cfg.context.merge_same_role)

        log.debug(
            "生成回复 session=%s reason=%s 历史=%d条 system=%d字",
            event.session_key,
            decision.reason,
            len(history),
            len(system_prompt),
        )

        collected: list[str] = []

        def _record_sent(message_id: str) -> None:
            self.store.mark_sent(event.session_key, [message_id])

        async def attempt(override: dict | None):
            return await self.sender.deliver(
                conn,
                event,
                self._pieces(messages, ctx, collected, timing, override),
                on_sent=_record_sent,
            )

        fallback = {
            "reasoning_effort": "none",
            "max_tokens": min(self.cfg.llm.max_tokens, 512),
        }
        used_fallback = False
        try:
            result = await attempt(None)
        except ThinkingBudgetExceeded as exc:
            used_fallback = True
            log.warning(
                "思考超预算 %d>%d，掐断后改用无思考模式重答 | %s | %s | msg=%s",
                exc.tokens,
                exc.budget,
                self._label(event),
                event.sender_label,
                event.message_id or "-",
                extra={"tag": "warn"},
            )
            result = await attempt(fallback)
        except LLMError as exc:
            log.error("模型请求失败: %s", exc)
            if collected:
                self._store_reply(event, collected)
            return
        except Exception:  # noqa: BLE001
            log.exception("回复流程异常 session=%s", event.session_key)
            if collected:
                self._store_reply(event, collected)
            return

        # 思考把配额吃光、正文一个字都没有：换无思考模式兜一次。
        if result.sent == 0 and not collected and not used_fallback:
            used_fallback = True
            log.warning(
                "模型只有思考没有正文，改用无思考模式重答 | %s | %s | msg=%s | max_tokens=%d | reasoning_tokens=%s | finish_reason=%s",
                self._label(event),
                event.sender_label,
                event.message_id or "-",
                self.cfg.llm.max_tokens,
                _reasoning_tokens(self.llm),
                self.llm.last_finish_reason or "-",
                extra={"tag": "warn"},
            )
            try:
                result = await attempt(fallback)
            except (LLMError, ThinkingBudgetExceeded) as exc:
                log.error("兜底重答也失败了: %s", exc)
                result = None

        self._cooldown[event.session_key] = time.time()
        if collected:
            self._store_reply(event, collected)
        elif result is None or result.sent == 0:
            log.warning(
                "重答后仍然没有正文，这条没有发出去 | %s | %s | msg=%s",
                self._label(event),
                event.sender_label,
                event.message_id or "-",
                extra={"tag": "warn"},
            )
        self.replies_sent += 1
        if timing.first_piece is not None:
            self._first_ms.append((timing.first_piece - timing.start) * 1000)

        # 发送的属性信息，和上面的“消息内容”分开，用 key=value 自有格式。
        log.info(
            "· 发送 session=%s 段=%d 引用=%s @=%s 首字=%s 总计=%.1fs %s%s",
            event.session_key,
            result.sent if result else 0,
            (result.reply_to if result and result.reply_to else "无"),
            (",".join(result.at_targets) if result and result.at_targets else "无"),
            f"{timing.first_ms:.0f}ms" if timing.first_ms is not None else "未记录",
            timing.total_ms / 1000,
            _usage_text(self.llm).strip(),
            " 兜底=无思考" if used_fallback else "",
            extra={"tag": "stat"},
        )

    async def _pieces(
        self,
        messages: list[dict],
        ctx: HookContext,
        collected: list[str],
        timing: _Timing,
        override: dict | None = None,
    ) -> AsyncIterator[str]:
        if not self.cfg.reply.segments:
            text = await self._complete(messages, override)
            if text.strip():
                timing.mark_first()
                out = await self.extensions.after_reply(ctx, text.strip())
                collected.append(out)
                yield out
            return

        segmenter = Segmenter(self.cfg.reply.segment_max_chars, self.cfg.reply.max_segments)
        async for delta in self._deltas(messages, override):
            timing.mark_first()
            for piece in segmenter.feed(delta):
                out = await self.extensions.after_reply(ctx, piece)
                collected.append(out)
                yield out
        for piece in segmenter.flush():
            out = await self.extensions.after_reply(ctx, piece)
            collected.append(out)
            yield out

    async def _deltas(self, messages: list[dict], override: dict | None = None) -> AsyncIterator[str]:
        if not self.cfg.llm.stream:
            text = await self._complete(messages, override)
            if text:
                yield text
            return
        guard = self._reasoning_guard()
        async for delta in self.llm.stream(messages, override=override, on_reasoning=guard):
            yield delta

    def _reasoning_guard(self):
        """思考超过预算就抛异常掐断流；返回 None 表示不限制。"""
        budget = self.cfg.llm.thinking_budget
        if budget <= 0:
            return None
        state = {"tokens": 0}

        def guard(chunk: str) -> None:
            state["tokens"] += estimate_text_tokens(chunk)
            if state["tokens"] > budget:
                raise ThinkingBudgetExceeded(state["tokens"], budget)

        return guard

    async def _complete(self, messages: list[dict], override: dict | None = None) -> str:
        return await self.llm.complete(messages, override=override)

    # ---------- 杂项 ----------

    def _store_reply(self, event: MessageEvent, collected: list[str]) -> None:
        text = clean_text("".join(collected), self.cfg.persona.nickname)
        if not text:
            return
        self.store.add(event.session_key, "assistant", text, speaker=self.cfg.persona.nickname)

    def _lock_for(self, session: str) -> asyncio.Lock:
        lock = self._locks.get(session)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session] = lock
        return lock

    def stats_line(self, *, connected: bool) -> str:
        uptime = max(time.time() - self.started_at, 0)
        hours, rem = divmod(int(uptime), 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            uptime_text = f"{hours}小时{minutes}分"
        elif minutes:
            uptime_text = f"{minutes}分{seconds}秒"
        else:
            uptime_text = f"{seconds}秒"
        avg = f"{sum(self._first_ms) / len(self._first_ms):.0f}ms" if self._first_ms else "—"
        state = "已连接" if connected else "未连接"
        return (
            f"─ 运行 {uptime_text} · SnowLuma {state} · 收到 {self.messages_seen} 条"
            f" · 回复 {self.replies_sent} 条 · 首字均值 {avg}"
        )

    def _cooldown_ok(self, session: str) -> bool:
        cooldown = self.cfg.trigger.cooldown
        if cooldown <= 0:
            return True
        last = self._cooldown.get(session)
        return last is None or (time.time() - last) >= cooldown

    def _label(self, event: MessageEvent) -> str:
        return event.target_label

    async def _group_label(self, event: MessageEvent) -> str:
        """群「群名」(群号)。群名走 get_group_info，缓存 10 分钟，失败 1 分钟后重试。"""
        if not event.is_group or event.group_id is None:
            return event.target_label

        gid = event.group_id
        now = time.time()
        cached = self._group_names.get(gid)
        if cached is not None:
            name, stamp = cached
            ttl = 600.0 if name != "未知群名" else 60.0
            if now - stamp < ttl:
                event.group_name = name
                return event.target_label

        name = "未知群名"
        conn = self.get_connection()
        if conn is not None:
            try:
                info = await conn.get_group_info(gid, timeout=3.0)
                name = str(info.get("group_name") or "").strip() or "未知群名"
            except Exception as exc:  # noqa: BLE001 - 查不到群名不影响回复
                log.debug("查询群名失败 group=%s: %s", gid, exc)
        self._group_names[gid] = (name, now)
        event.group_name = name
        return event.target_label

    def _maybe_prune(self, session: str) -> None:
        counter = self._since_prune.get(session, 0) + 1
        self._since_prune[session] = counter
        if counter < 50:
            return
        self._since_prune[session] = 0
        removed = self.store.prune(session, keep=max(self.cfg.context.max_messages * 2, 200))
        if removed:
            log.info("清理旧消息 session=%s 删除 %d 条", session, removed)


def _usage_text(llm: LLMClient) -> str:
    usage = llm.last_usage or {}
    if not usage:
        return ""
    prompt = usage.get("prompt_tokens", "?")
    completion = usage.get("completion_tokens")
    details = usage.get("completion_tokens_details") or {}
    reasoning = details.get("reasoning_tokens")
    text = f"tokens {prompt}/{completion if completion is not None else '?'}"
    if reasoning:
        answer = ""
        if isinstance(completion, int) and isinstance(reasoning, int):
            answer = f"，正文 {max(completion - reasoning, 0)}"
        text += f"（思考 {reasoning}{answer}）"
    return text


def _reasoning_tokens(llm: LLMClient) -> int | None:
    details = (llm.last_usage or {}).get("completion_tokens_details") or {}
    value = details.get("reasoning_tokens")
    return value if isinstance(value, int) else None


def _prompt_text(event: MessageEvent, *, quoted: bool = False) -> str:
    """纯 @ 或空消息也要给模型一句人话，不然它面对空 user 消息会乱编。"""
    text = event.content.text.strip()
    if not text:
        if event.content.images:
            text = ""
        elif event.is_group and event.content.at_targets:
            text = "（只@了你一下，没说话）"
        else:
            text = "（发来一条空消息）"
    if quoted:
        prefix = "（他引用了你之前说的话）"
        return f"{prefix}\n{text}" if text else prefix
    return text
