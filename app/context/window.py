"""从 SQLite 消息里挑出送进模型的历史，按条数和 token 预算双重裁剪。"""

from __future__ import annotations

from typing import Any

from ..config import ContextConfig
from ..llm.tokens import estimate_text_tokens
from .store import StoredMessage


def _render(message: StoredMessage, is_group: bool) -> str:
    text = message.content.strip()
    if not text:
        # 纯图片/表情消息在历史里没有任何文字信息，塞 “[图片]” 只会让模型以为图坏了。
        return ""
    if message.role == "user" and is_group:
        speaker = message.speaker or (str(message.user_id) if message.user_id else "某人")
        return f"{speaker}: {text}"
    return text


def build_history(messages: list[StoredMessage], cfg: ContextConfig, *, is_group: bool) -> list[dict[str, Any]]:
    """messages 为从旧到新的顺序，返回同样顺序的 chat messages（不含当前这条）。"""
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "assistant" and not cfg.include_bot_replies:
            continue
        text = _render(message, is_group)
        if not text:
            continue
        items.append({"role": message.role, "content": text})

    # token 预算：从新到旧保留，超预算就丢最旧的。
    budget = max(cfg.max_tokens, 256)
    kept: list[dict[str, Any]] = []
    used = 0
    for item in reversed(items):
        cost = estimate_text_tokens(item["content"]) + 4
        if kept and used + cost > budget:
            break
        kept.append(item)
        used += cost
    kept.reverse()
    return kept
