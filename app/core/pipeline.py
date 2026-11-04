"""消息处理主流程：触发判断 → 上下文 → 单次模型请求 → 分段发送。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable

from ..config import Config
from ..context.store import MessageStore
from ..context.window import build_ambient, build_history, keep_addressed, pick_anchor_window
from ..llm.client import LLMClient, LLMError, ThinkingBudgetExceeded
from ..llm.prompt import build_messages, build_system_prompt, scene_line
from ..llm.tokens import estimate_text_tokens
from ..media.images import ImageLoader
from ..onebot.client import OneBotConnection
from ..onebot.events import MessageEvent
from ..onebot.members import MemberDirectory
from ..onebot.protocol import ImageRef
from ..persona import Persona
from ..pluginkit.registry import ExtensionRegistry, HookContext
from ..reply.segments import Segmenter
from ..reply.sender import ReplySender, clean_text
from ..sdk import CommandContext
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
        members: MemberDirectory,
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
        self.members = members
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
        self._anchors: dict[str, int] = {}
        self._llm_slot = asyncio.Semaphore(max(cfg.llm.max_concurrency, 1))

    # ---------- 入口 ----------

    async def handle(self, event: MessageEvent) -> None:
        session = event.session_key
        if event.message_id and self.store.seen(session, event.message_id):
            log.debug("重复事件，跳过 message_id=%s", event.message_id)
            return

        conn = self.get_connection()
        # 命令优先：命中插件命令就直接回，不进模型、不进历史。
        if conn is not None and await self._run_command(conn, event, session):
            return
        if conn is None and self._looks_like_command(event):
            log.warning("当前没有 OneBot 连接，命令丢弃 session=%s", session)
            return

        decision = decide(
            self.cfg,
            event,
            self.get_self_id(),
            lambda mid: self.store.is_sent(session, mid),
        )

        group_label = await self._group_label(event)
        # 引用了一张图、但当前消息本身只有文字：把那张图取过来。
        # 群里常用的姿势是“先发图，再引用那张图说一句”，不取的话模型只会回“图呢？”。
        # 只有真要回复时才去取，否则每次引用都要多打一次 get_msg。
        if decision.should and conn is not None and not event.content.images and event.reply_id:
            try:
                quoted = await self._quoted_images(conn, event.reply_id)
                if quoted:
                    event.content.images.extend(quoted)
                    log.info("从引用里取到 %d 张图片 reply=%s", len(quoted), event.reply_id, extra={"tag": "sys"})
            except Exception:  # noqa: BLE001 - 取不到就当作没有图
                log.debug("取引用图片失败 reply=%s", event.reply_id, exc_info=True)
        if conn is not None and event.is_group and event.group_id is not None:
            # 把 at 123456 还原成 at 群名片(123456)，模型才知道在叫谁
            try:
                event.content.text = await self.members.expand(conn, event.group_id, event.content.text)
                if event.self_id:
                    event.self_name = await self.members.name_of(conn, event.group_id, event.self_id)
            except Exception:  # noqa: BLE001 - 展开失败不影响回复
                log.debug("展开 at 失败", exc_info=True)
        preview = event.content.text.strip()[:60] or ("[图片]" if event.content.images else "")
        if event.content.images:
            # 注意：这里只是“消息里带了图”，不代表要下载。
            # 真正的下载发生在要回复时（见下面的 images.prepare），所以平时只打一行短日志。
            if decision.should:
                # OneBot 实现之间字段差异很大：有的只给 url，有的只给 file（本地文件名）。
                # 图片出问题时先看这一行，才知道是没解析出来还是没下载下来。
                log.info(
                    "图片 %d 张: %s",
                    len(event.content.images),
                    " | ".join(
                        f"url={(ref.url or '-')[:90]} file={ref.file or '-'}" for ref in event.content.images
                    ),
                    extra={"tag": "sys"},
                )
            else:
                log.debug("图片 %d 张（不在跟它说话，跳过下载）", len(event.content.images))
        self.messages_seen += 1
        at_field = await self._at_field(conn, event)
        if decision.should:
            log.info(
                "← %s | %s | msg=%s%s%s | %s",
                group_label,
                event.sender_label,
                event.message_id or "-",
                f" | 引用={event.reply_id}" if event.reply_id else "",
                f" | at={at_field}" if at_field else "",
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
        history = self._history_window(session)
        ambient = self._ambient_context(session) if event.is_group else ""
        # 图只在真要回这条消息时才下载解析。群里绝大部分图跟它无关，
        # 之前的写法是每张都下、每张都解码，日志里一片“图片 N 张”白干活。
        prepared = await self.images.prepare(event.content.images) if decision.should else []
        user_row_id = self.store.add(
            session,
            "user",
            event.content.text,
            speaker=event.sender_name,
            user_id=event.user_id,
            message_id=event.message_id,
            has_image=bool(event.content.images),
            images=[str(image.path) for image in prepared],
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

        if conn is None:
            log.warning("当前没有 OneBot 连接，丢弃回复 session=%s", session)
            return

        async with self._lock_for(session):
            await self._reply(
                conn,
                event,
                history,
                ctx,
                decision,
                prepared=prepared,
                trigger_id=user_row_id,
                ambient=ambient,
            )

    # ---------- 命令 ----------

    def _command_prefix(self) -> str:
        return (self.cfg.commands.prefix or "/").strip()

    def _looks_like_command(self, event: MessageEvent) -> bool:
        prefix = self._command_prefix()
        return bool(prefix) and (event.content.text or "").strip().startswith(prefix)

    async def _send_plain(self, conn: OneBotConnection, event: MessageEvent, text: str) -> None:
        """命令的回复走普通发送链路：引用、分段、去重都照旧。"""
        body = (text or "").strip()

        async def pieces():
            if body:
                yield body

        try:
            await self.sender.deliver(
                conn,
                event,
                pieces(),
                on_sent=lambda mid: self.store.mark_sent(event.session_key, [mid]),
            )
        except Exception:  # noqa: BLE001 - 命令回复失败不能连带主流程
            log.exception("命令回复发送失败")

    async def _run_command(self, conn: OneBotConnection, event: MessageEvent, session: str) -> bool:
        """命中插件命令就执行并回复，返回 True 表示这条消息到此为止。"""
        if not self._looks_like_command(event):
            return False
        prefix = self._command_prefix()
        body = event.content.text.strip()[len(prefix) :].strip()
        if not body:
            return False
        name, _, rest = body.partition(" ")
        found = self.extensions.find_command(name)
        if found is None:
            return False  # 不是插件命令，交给正常触发流程
        plugin, command = found

        admins = {str(item).strip() for item in self.cfg.commands.admins if str(item).strip()}
        is_admin = not admins or str(event.user_id) in admins
        if command.admin and not is_admin:
            await self._send_plain(conn, event, "这个命令只有管理员能用")
            return True

        cmd_ctx = CommandContext(
            plugin=plugin,
            name=name,
            args=rest.split(),
            text=rest,
            event=event,
            session_key=session,
            is_group=event.is_group,
            is_admin=is_admin,
        )
        log.info("命令 /%s 由 %s 触发（插件 %s）", name, event.sender_name, plugin, extra={"tag": "sys"})
        try:
            reply = await asyncio.wait_for(
                command.handler(cmd_ctx), timeout=max(self.cfg.commands.timeout, 1.0)
            )
        except asyncio.TimeoutError:
            reply = "命令超时了"
        except Exception as exc:  # noqa: BLE001 - 单个命令炸了不影响别人
            log.exception("命令 /%s 执行失败", name)
            reply = f"命令执行失败：{exc}"
        await self._send_plain(conn, event, reply or "")
        return True

    # ---------- 回复 ----------

    async def _reply(
        self,
        conn: OneBotConnection,
        event: MessageEvent,
        history_messages: list,
        ctx: HookContext,
        decision: Decision,
        prepared: list | None = None,
        trigger_id: int | None = None,
        ambient: str = "",
    ) -> None:
        timing = _Timing()
        budget = self.cfg.context.max_tokens
        cap = self.cfg.llm.max_context
        if cap > 0:
            # 软件侧的硬上限：prompt 再长也不许超过模型窗口的一半，留出生长的余地。
            budget = min(budget, max(int(cap * 0.5), 256))
        history = build_history(
            history_messages, self.cfg.context, is_group=event.is_group, budget_override=budget
        )
        history = self._attach_history_images(history)
        ctx.history = history

        text = _prompt_text(
            event,
            quoted=decision.reason == "quote",
            cfg=self.cfg,
            ambient=ambient,
        )
        suffix = await self.extensions.collect_user_suffix(ctx)
        if suffix:
            text = f"{text}\n\n{suffix}" if text else suffix
        content = await self.images.build_content(
            text,
            prepared if prepared is not None else [],
        )
        extra = await self.extensions.collect_system_prompt(ctx)
        system_prompt = build_system_prompt(self.cfg, self.persona.text, extra_sections=extra)
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
            # 全局串行：LM Studio 有多槽位，两条请求并发会落到不同 slot，
            # 各自的 KV 缓存互相无效，而且抢显存把生成速度从 62 t/s 掉到 22 t/s。
            async with self._llm_slot:
                return await self.sender.deliver(
                    conn,
                    event,
                    self._pieces(messages, ctx, collected, timing, override),
                    on_sent=_record_sent,
                )

        fallback = {
            "reasoning_effort": "none",
            # 正常请求可以不发 max_tokens（跟 AstrBot 一样），兜底这一路必须自己封顶，
            # 否则无思考模式下模型可能一直说下去。
            "max_tokens": min(self.cfg.llm.max_tokens, 512) if self.cfg.llm.max_tokens > 0 else 512,
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
                self._store_reply(event, collected, trigger_id)
            return
        except Exception:  # noqa: BLE001
            log.exception("回复流程异常 session=%s", event.session_key)
            if collected:
                self._store_reply(event, collected, trigger_id)
            return

        # 思考把配额吃光、正文一个字都没有：换无思考模式兜一次。
        if result.sent == 0 and not collected and not used_fallback:
            used_fallback = True
            log.warning(
                "模型只有思考没有正文，改用无思考模式重答 | %s | %s | msg=%s | max_tokens=%d | "
                "reasoning_tokens=%s | finish_reason=%s",
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
            # 写回上下文的是“实际发出去的文本”（at 段已渲染成 at 名字(QQ)），
            # 不是模型原文。否则历史里会保留模型写的 @，下一轮它会照着学。
            texts = result.texts if result is not None and result.texts else collected
            self._store_reply(event, texts, trigger_id)
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
            "· 发送 session=%s 段=%d 引用=%s at=%s 首字=%s 总计=%.1fs %s%s",
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

    def _store_reply(self, event: MessageEvent, collected: list[str], reply_to: int | None = None) -> None:
        text = clean_text("".join(collected), self.cfg.persona.nickname)
        if not text:
            return
        self.store.add(
            event.session_key,
            "assistant",
            text,
            speaker=self.cfg.persona.nickname,
            reply_to=reply_to,
        )

    def _lock_for(self, session: str) -> asyncio.Lock:
        lock = self._locks.get(session)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session] = lock
        return lock

    @property
    def average_first_ms(self) -> float:
        return sum(self._first_ms) / len(self._first_ms) if self._first_ms else 0.0

    @property
    def uptime_seconds(self) -> float:
        return max(time.time() - self.started_at, 0.0)

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

    async def _at_field(self, conn: OneBotConnection | None, event: MessageEvent) -> str:
        """日志里的 at= 字段：at 名字(QQ)，和消息正文同一种写法，不出现 @。"""
        if not event.at_targets:
            return ""
        parts: list[str] = []
        for qq in event.at_targets:
            name = None
            if conn is not None and event.group_id is not None and qq.isdigit():
                try:
                    name = await self.members.name_of(conn, event.group_id, int(qq))
                except Exception:  # noqa: BLE001 - 只是日志
                    name = None
            parts.append(f"{name}({qq})" if name else qq)
        return "、".join(parts)

    def _attach_history_images(self, history: list[dict]) -> list[dict]:
        """把最近几张历史图重新塞回它们原来的那一轮。

        只带最后 `images.history_images` 张：每张 512 边长约 250~400 token，
        而且每加一张新的、最旧的会被挤掉，前缀变一次就全量重算一次。
        """
        limit = self.cfg.images.history_images
        for item in history:
            paths = item.pop("images", None)
            if not paths or limit <= 0:
                continue
            if limit <= 0:
                continue
            item["_pending_images"] = paths

        used = 0
        for item in reversed(history):
            paths = item.pop("_pending_images", None)
            if not paths or used >= limit:
                continue
            take = paths[: limit - used]
            used += len(take)
            urls = [url for raw in take if (url := ImageLoader.load_image(Path(raw)))]
            if not urls:
                continue
            parts: list[dict] = [{"type": "text", "text": item["content"]}]
            parts.extend({"type": "image_url", "image_url": {"url": url}} for url in urls)
            item["content"] = parts
        return history

    def _history_window(self, session: str):
        """滑动窗口改锚点窗口，让 KV 缓存能命中。

        原来每来一条消息就 recent(100)，最旧那条被挤掉，前缀整个位移，
        llama.cpp 只能从零重新预填充（实测 1668 token ≈ 2.5 秒）。
        这里改成按锚点取：窗口从 60% 涨到 125% 才推进一次锚点，
        平时只是往尾部追加，前缀不动，缓存就能复用。

        注意必须取“锚点之后最新的 N 条”，不能取最早的 N 条：
        `store.after()` 是最早优先，历史一旦超过上限，窗口就会停在旧消息上，
        最新的对话根本进不来（实测落后 431 条，模型看起来像鱼的记忆）。
        """
        limit = max(self.cfg.context.max_messages, 20)
        hard = int(limit * 1.25)
        anchor = self._anchors.get(session, 0)
        # latest_after 的 headroom 要明显大于上限；平时 prune 只留 200 条左右，
        # 这里给 4 倍余量，历史再长也不会把最新的截掉。
        headroom = max(hard * 4, 512)
        messages = self.store.latest_after(session, anchor, headroom)
        if self.cfg.context.only_addressed:
            messages = keep_addressed(messages)
        messages, new_anchor = pick_anchor_window(messages, limit)
        if new_anchor is not None:
            self._anchors[session] = new_anchor
            log.debug("推进上下文锚点 session=%s 保留 %d 条", session, len(messages))
        return messages

    def _ambient_context(self, session: str) -> str:
        """上次回复之后的群聊，一次性注入当前消息；不进入持久历史。"""
        last = self.store.last_role_id(session, "assistant")
        messages = self.store.latest_after(session, last, 200)
        return build_ambient(messages)

    async def _quoted_images(self, conn: OneBotConnection, reply_id: str) -> list:
        """用 get_msg 把被引用那条消息里的图捞出来。拿不到就返回空。"""
        try:
            message_id: object = int(reply_id) if reply_id.lstrip("-").isdigit() else reply_id
        except (TypeError, ValueError):
            message_id = reply_id
        data = await conn.call_action("get_msg", {"message_id": message_id}, 5.0)
        if not isinstance(data, dict):
            return []
        segments = data.get("message")
        if not isinstance(segments, list):
            return []
        images = []
        for segment in segments:
            if not isinstance(segment, dict) or segment.get("type") != "image":
                continue
            payload = segment.get("data") or {}
            url, file = payload.get("url"), payload.get("file")
            if url or file:
                images.append(ImageRef(url=url, file=file, summary=payload.get("summary")))
            if len(images) >= self.cfg.images.max_per_message:
                break
        return images

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
    text = f"tokens in={prompt} out={completion if completion is not None else '?'}"
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


def _prompt_text(
    event: MessageEvent,
    *,
    quoted: bool = False,
    cfg: Config | None = None,
    ambient: str = "",
) -> str:
    """当前这条 user 消息：场景行 + 正文。

    场景信息（时间/群/说话人）必须放在这里而不是 system——放 system 里每句都变，
    会让 KV 缓存整段失效，实测每次多花 2~3 秒重新预填充。
    """
    text = event.content.text.strip()
    if event.content.only_mentions:
        text = "（只@了你一下，没说话）"
    elif not text:
        if event.content.images:
            # 图没加载出来也要让模型知道“对方发了图”，否则它会直接答“我看不到图”。
            text = "（对方发来一张图片）"
        elif event.is_group and event.content.at_targets:
            text = "（只@了你一下，没说话）"
        else:
            text = "（发来一条空消息）"

    lines: list[str] = []
    if cfg is not None:
        lines.append(
            scene_line(
                cfg,
                is_group=event.is_group,
                group_id=event.group_id,
                group_name=event.group_name,
                speaker=event.sender_name,
                speaker_id=event.user_id,
                self_id=event.self_id or None,
                self_name=event.self_name,
            )
        )
    if ambient:
        lines.append(ambient)
    if quoted:
        lines.append("（他引用了你之前说的话）")
    if text:
        lines.append(text)
    return "\n".join(lines)





