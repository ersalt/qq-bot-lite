"""从 SQLite 消息里挑出送进模型的历史，按条数和 token 预算双重裁剪。"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from ..config import ContextConfig
from ..llm.tokens import estimate_text_tokens
from .store import StoredMessage


def _stamp(message: StoredMessage) -> str:
    return _dt.datetime.fromtimestamp(message.created_at).strftime("%H:%M")


_REPLY_PREFIX_RE = re.compile(
    r"^\s*[（(]\s*回\s*[「“\"]?[^」”\"）)\n]{1,40}[」”\"]?\s*[）)]\s*"
)


def _render(
    message: StoredMessage,
    is_group: bool,
    timestamps: bool,
) -> str:
    text = message.content.strip()
    if message.role == "assistant":
        # 老数据里可能存着「（回 昵称）」标注；历史里清掉，否则模型会照学。
        text = _REPLY_PREFIX_RE.sub("", text)
    if not text:
        # 纯图片消息也要在历史里占一行，否则模型不知道那时候发过图。
        return "[图片]" if getattr(message, "images", None) else ""
    if message.role == "user" and is_group:
        speaker = message.speaker or (str(message.user_id) if message.user_id else "某人")
        prefix = f"[{_stamp(message)}] " if timestamps else ""
        return f"{prefix}「{speaker}」: {text}"
    return text


def keep_addressed(messages: list[StoredMessage]) -> list[StoredMessage]:
    """只留“它接过的轮次”，群里的闲话交给 build_ambient。

    AstrBot 的历史就是这么攒的：只有被它接了的话才进持久对话历史，群里的闲话
    作为一次性的 ambient 块注入当前消息（注入完就清）。我们以前把尾部群聊也塞进
    历史，模型会把闲话当成连续对话，还容易把回复算到别人头上。

    assistant 消息现在带 reply_to（触发它的 user 消息 id）。按 id 配对，
    而不是“紧挨着 assistant 的那条 user”：群里有人插话时，紧挨着的很可能是
    另一个人的闲话，会把回复算到错的人头上。
    """
    by_id = {message.id: message for message in messages}
    kept_ids: set[int] = set()
    for index, message in enumerate(messages):
        if message.role != "assistant":
            continue
        kept_ids.add(message.id)
        if message.reply_to and message.reply_to in by_id:
            kept_ids.add(message.reply_to)
            continue
        # 老数据没有 reply_to，只能退化成“紧挨着的那条 user”
        if index > 0 and messages[index - 1].role == "user":
            kept_ids.add(messages[index - 1].id)
    return [message for message in messages if message.id in kept_ids]


AMBIENT_TOKEN_BUDGET = 800


def build_ambient(
    messages: list[StoredMessage],
    *,
    budget: int = AMBIENT_TOKEN_BUDGET,
    self_id: int | None = None,
) -> str:
    """把上次回复之后的群聊拼成一次性 <system_reminder> 块。

    这不是持久历史：调用方只把它注入当前 user 消息，不写回 store。下一条 assistant
    落库后，起点自然前移，旧块就没了。
    """
    lines: list[str] = []
    used = 0
    for message in reversed(messages):
        if message.role != "user":
            continue
        rendered = _render(message, True, True)
        if not rendered:
            if not message.has_image:
                continue
            speaker = message.speaker or (str(message.user_id) if message.user_id else "某人")
            rendered = f"[{_stamp(message)}] 「{speaker}」: [图片]"
        elif message.has_image and "[图片]" not in rendered:
            rendered += " [图片]"
        if self_id and "at " in rendered and f"({self_id})" in rendered:
            rendered = "⚠️[DIRECTED AT YOU] " + rendered
        cost = estimate_text_tokens(rendered) + 2
        if lines and used + cost > budget:
            break
        lines.append(rendered)
        used += cost
    if not lines:
        return ""
    lines.reverse()
    return (
        "<system_reminder>\n"
        "你上次回复之后，群里的聊天记录（只是背景，不要逐条回应）：\n"
        + "\n".join(lines)
        + "\n--- END CONTEXT ---\n</system_reminder>"
    )


def pick_anchor_window(
    messages: list[StoredMessage], limit: int
) -> tuple[list[StoredMessage], int | None]:
    """按 100%~125% 水位挑锚点窗口。

    返回 (窗口, 新锚点)；新锚点为 None 表示还没到上限，不推进。
    比上限多时一次退到上限（而不是 60%），避免 50 条突然掉到 24 条那种
    大幅失忆；代价是锚点推进频率略高，但上下文连续性更好。
    """
    hard = max(int(limit * 1.25), 10)
    if len(messages) <= hard:
        return messages, None
    keep_count = max(limit, 10)
    start = max(len(messages) - keep_count, 0)
    # 别从半轮开始：如果切在 assistant 上，把它前面配对的 user 一起带上，
    # 否则模型会看到一句没头没尾的“自己说过的话”。
    while start > 0 and messages[start].role == "assistant":
        start -= 1
    keep = messages[start:]
    return keep, keep[0].id - 1


def build_history(
    messages: list[StoredMessage],
    cfg: ContextConfig,
    *,
    is_group: bool,
    budget_override: int | None = None,
) -> list[dict[str, Any]]:
    """messages 为从旧到新的顺序，返回同样顺序的 chat messages（不含当前这条）。"""
    items: list[dict[str, Any]] = []
    by_id = {message.id: message for message in messages}
    for message in messages:
        if message.role == "assistant" and not cfg.include_bot_replies:
            continue
        text = _render(message, is_group, cfg.timestamps)
        if not text:
            continue
        item: dict[str, Any] = {"role": message.role, "content": text}
        if message.role == "assistant" and message.reply_to:
            target = by_id.get(message.reply_to)
            if target is not None and target.role == "user":
                # 只作为内部合并边界，不发给模型；避免把回给 A 和回给 B 的
                # 两条 assistant 合并成一段，模型就分不清哪句在回谁。
                item["_reply_to"] = target.speaker or (
                    str(target.user_id) if target.user_id else "某人"
                )
        if message.images:
            # 路径留着，发请求前由 pipeline 决定要不要把图真的带上。
            item["images"] = list(message.images)
        items.append(item)

    # token 预算：从新到旧保留，超预算就丢最旧的。
    # 注意这里是"一次退到 60%"，不是一条一条挤：窗口每轮只掉一条的话前缀每轮都在动，
    # llama.cpp 的前缀缓存就永远命不中，预填充等于每次全量重来。
    budget = max(budget_override if budget_override is not None else cfg.max_tokens, 256)
    total = sum(estimate_text_tokens(item["content"]) + 4 for item in items)
    if total > budget:
        target = int(budget * 0.6)
        kept: list[dict[str, Any]] = []
        used = 0
        for item in reversed(items):
            cost = estimate_text_tokens(item["content"]) + 4
            if kept and used + cost > target:
                break
            kept.append(item)
            used += cost
        kept.reverse()
        return kept
    return items

