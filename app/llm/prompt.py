"""提示词拼装：只有一个 system 在开头，其余全是 user/assistant。

这一条是有意为之：Qwen 系模板要求 “System message must be at the beginning”，
把动态内容塞进历史中的 system 会直接 500，qq-maid-bot 当初就是靠 1235 代理绕的。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..config import Config

RULES = """\
你在 QQ 上和人聊天，不是写文章。
- 像真人发消息：一次一两句，口语，可以有语气词和情绪。
- 不要用 Markdown：不要标题、列表、加粗、代码块、表情符号堆砌。
- 不要自己写 “@昵称” 或 [CQ:...]：@ 和引用由程序自动处理，你写了会被原样发出去。
- 不要复述对方的话，不要自称 AI 或助手，不要问“还有什么可以帮你的”。
- 对方发图就针对图里的内容回应；没看懂就问一句。
- 想清楚就直接说，别在心里反复推翻自己；纠结太久会显得很奇怪。
"""

_WEEKDAYS = "一二三四五六日"


def scene_block(cfg: Config, *, is_group: bool, group_id: int | None, speaker: str) -> str:
    now = _dt.datetime.now()
    where = f"QQ 群（群号 {group_id}）" if is_group else "QQ 私聊"
    return (
        f"当前场景：{where}。\n"
        f"你的昵称：{cfg.persona.nickname}。\n"
        f"正在和你说话的人：{speaker}。\n"
        f"现在时间：{now:%Y-%m-%d %H:%M} 周{_WEEKDAYS[now.weekday()]}。"
    )


def build_system_prompt(
    cfg: Config,
    persona_text: str,
    *,
    is_group: bool,
    group_id: int | None,
    speaker: str,
    extra_sections: list[str] | None = None,
) -> str:
    blocks = [persona_text.strip(), RULES.strip(), scene_block(cfg, is_group=is_group, group_id=group_id, speaker=speaker)]
    for section in extra_sections or []:
        if section and section.strip():
            blocks.append(section.strip())
    return "\n\n".join(blocks)


def merge_adjacent(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并连续同角色消息，模板更稳、token 更省。"""
    merged: list[dict[str, Any]] = []
    for msg in messages:
        if (
            merged
            and merged[-1]["role"] == msg["role"]
            and isinstance(merged[-1]["content"], str)
            and isinstance(msg["content"], str)
        ):
            merged[-1]["content"] = f"{merged[-1]['content']}\n{msg['content']}"
        else:
            merged.append(dict(msg))
    return merged


def build_messages(
    system_prompt: str,
    history: list[dict[str, Any]],
    current: str | list[dict[str, Any]],
    merge_same_role: bool = True,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": current})

    body = messages[1:]
    if merge_same_role:
        body = merge_adjacent(body)
    return [messages[0], *body]
