"""是否要回复这条消息。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..config import Config
from ..onebot.events import MessageEvent


@dataclass(slots=True)
class Decision:
    should: bool
    reason: str


def decide(
    cfg: Config,
    event: MessageEvent,
    self_id: int | None,
    is_reply_to_me: Callable[[str], bool] | None = None,
) -> Decision:
    if self_id is not None and event.user_id == self_id:
        return Decision(False, "self")

    text = event.content.text.strip()

    # 只 @ 不说人话也算点名——先判 @，再判空。
    if event.is_group and cfg.trigger.group_at and self_id is not None and event.addressed_to_me(self_id):
        return Decision(True, "at")

    # 引用回复了机器人的消息，等于在跟它说话，哪怕没 @。
    if event.content.reply_to and is_reply_to_me and is_reply_to_me(event.content.reply_to):
        return Decision(True, "quote")

    if not text and not event.content.images:
        return Decision(False, "empty")

    if not event.is_group:
        return Decision(cfg.trigger.private_always, "private" if cfg.trigger.private_always else "private-off")

    prefix = cfg.trigger.group_prefix.strip()
    if prefix and text.startswith(prefix):
        return Decision(True, "prefix")

    lowered = text.casefold()
    for keyword in cfg.trigger.group_keywords:
        key = keyword.strip()
        if key and key.casefold() in lowered:
            return Decision(True, f"keyword:{key}")

    nickname = cfg.persona.nickname.strip()
    if nickname and nickname.casefold() in lowered:
        return Decision(True, "nickname")

    return Decision(False, "no-trigger")
