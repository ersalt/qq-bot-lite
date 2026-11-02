"""从 SQLite 消息里挑出送进模型的历史，按条数和 token 预算双重裁剪。"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..config import ContextConfig
from ..llm.tokens import estimate_text_tokens
from .store import StoredMessage


def _stamp(message: StoredMessage) -> str:
    return _dt.datetime.fromtimestamp(message.created_at).strftime("%H:%M")


def _render(message: StoredMessage, is_group: bool, timestamps: bool) -> str:
    text = message.content.strip()
    if not text:
        # 纯图片/表情消息在历史里没有任何文字信息，塞 “[图片]” 只会让模型以为图坏了。
        return ""
    if message.role == "user" and is_group:
        speaker = message.speaker or (str(message.user_id) if message.user_id else "某人")
        prefix = f"[{_stamp(message)}] " if timestamps else ""
        return f"{prefix}{speaker}: {text}"
    return text


def build_history(
    messages: list[StoredMessage],
    cfg: ContextConfig,
    *,
    is_group: bool,
) -> list[dict[str, Any]]:
    """messages 为从旧到新的顺序，返回同样顺序的 chat messages（不含当前这条）。"""
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "assistant" and not cfg.include_bot_replies:
            continue
        text = _render(message, is_group, cfg.timestamps)
        if not text:
            continue
        items.append({"role": message.role, "content": text})

    # token 预算：从新到旧保留，超预算就丢最旧的。
    # 注意这里是"一次退到 60%"，不是一条一条挤：窗口每轮只掉一条的话前缀每轮都在动，
    # llama.cpp 的前缀缓存就永远命不中，预填充等于每次全量重来。
    budget = max(cfg.max_tokens, 256)
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
