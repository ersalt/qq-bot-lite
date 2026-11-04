"""从 SQLite 消息里挑出送进模型的历史，按条数和 token 预算双重裁剪。"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..config import ContextConfig
from ..llm.tokens import estimate_text_tokens
from .store import StoredMessage


def _stamp(message: StoredMessage) -> str:
    return _dt.datetime.fromtimestamp(message.created_at).strftime("%H:%M")


def _render(
    message: StoredMessage,
    is_group: bool,
    timestamps: bool,
    addressee: str | None = None,
) -> str:
    text = message.content.strip()
    if not text:
        # 纯图片消息也要在历史里占一行，否则模型不知道那时候发过图。
        return "[图片]" if getattr(message, "images", None) else ""
    if message.role == "user" and is_group:
        speaker = message.speaker or (str(message.user_id) if message.user_id else "某人")
        prefix = f"[{_stamp(message)}] " if timestamps else ""
        return f"{prefix}{speaker}: {text}"
    if message.role == "assistant" and is_group and addressee:
        return f"（回 {addressee}）{text}"
    return text


def keep_addressed(messages: list[StoredMessage]) -> list[StoredMessage]:
    """只留“它接过的轮次”，外加最后一段还没回应的群聊。

    AstrBot 的历史就是这么攒的：只有被它接了的话才进对话历史，群里的闲话不进
    （它以一条一次性的转录块补上，注入完就删）。我们原来是全员闲聊全塞进去，
    模型每轮都要先把几十条跟它无关的话读一遍再判断谁在说什么 —— 实测思考量
    会从 100 上下涨到 267~514，正好是那 4~6 秒的来源。

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

    # 末尾那段还没被回应的群聊保留，否则模型不知道“刚才聊到哪了”。
    # 这一段是它这次要接的上下文，和 AstrBot 的 backlog 块等价。
    if messages and messages[-1].role != "assistant":
        start = len(messages) - 1
        while start > 0 and messages[start - 1].role != "assistant":
            start -= 1
        kept_ids.update(message.id for message in messages[start:])
    return [message for message in messages if message.id in kept_ids]


def pick_anchor_window(
    messages: list[StoredMessage], limit: int
) -> tuple[list[StoredMessage], int | None]:
    """按 60%~125% 水位挑锚点窗口。

    返回 (窗口, 新锚点)；新锚点为 None 表示还没到上限，不推进。
    比上限多时一次退到 60%，让前缀稳定一段时间，而不是每条挤掉一条。
    """
    hard = max(int(limit * 1.25), 10)
    if len(messages) <= hard:
        return messages, None
    keep_count = max(int(limit * 0.6), 10)
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
    by_id = {message.id: message for message in messages}
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "assistant" and not cfg.include_bot_replies:
            continue
        addressee = None
        if message.role == "assistant" and message.reply_to:
            trigger = by_id.get(message.reply_to)
            if trigger is not None:
                addressee = trigger.speaker or (
                    str(trigger.user_id) if trigger.user_id else None
                )
        text = _render(message, is_group, cfg.timestamps, addressee=addressee)
        if not text:
            continue
        item: dict[str, Any] = {"role": message.role, "content": text}
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

