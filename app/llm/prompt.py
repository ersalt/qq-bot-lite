"""提示词拼装：只有一个 system 在开头，其余全是 user/assistant。

两条硬约束：

1. Qwen 系模板要求 “System message must be at the beginning”，动态内容塞进历史里的
   system 会直接 500（qq-maid-bot 当初就是靠 1235 代理绕的）。
2. **system 必须逐字节稳定**。llama.cpp 的 KV 缓存按前缀匹配，system 里哪怕只改一个
   时间戳，整段上下文都要重新预填充。实测 1600 token 的上下文重算一次多花 2~3 秒。
   所以时间、群名、说话人这些每句都变的东西放在**当前这条 user 消息**里，不进 system。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..config import Config

RULES = """\
以上人设来自群主，风格（篇幅、口语、Markdown）以人设里的“回复风格”为准。下面是程序约定，必须遵守：

- 不要写 [CQ:...]。要叫群里某个人的时候写 “at 昵称”（用群名片上的名字），程序会换成真正的 @。
- 不要 at 你自己。消息里的 “at 名字(123456)” 是别人在 @ 人，括号里是 QQ 号；你自己是哪个号，看场景行里的 self。
- 对方发图就针对图里的内容回应；没看懂就直接问一句。
- 不知道的事就说不知道，别硬编，也别编造群友的身份。
- 想清楚就直接说，别在心里反复推翻自己；纠结太久会显得很奇怪。
- 思考的时候别复述人设和聊天记录，也别用英文分析，想一句“怎么回”就停。
"""

_WEEKDAYS = "一二三四五六日"


def scene_line(
    cfg: Config,
    *,
    is_group: bool,
    group_id: int | None,
    group_name: str | None,
    speaker: str,
    speaker_id: int | None = None,
    self_id: int | None = None,
    self_name: str | None = None,
) -> str:
    """当前这条 user 消息前面挂的上下文块。

    用 key: value 的标签格式（AstrBot 也是这么做的），比一句中文长句更好解析；
    放在 user 消息里而不是 system，才不会破坏 KV 缓存。
    """
    now = _dt.datetime.now()
    name = self_name or cfg.persona.nickname
    who = f"{speaker} ({speaker_id})" if speaker_id else speaker
    me = f"{name} ({self_id})" if self_id else name
    where = f"{group_name or '未知群名'} ({group_id})" if is_group else "private"
    return (
        "<ctx>\n"
        f"time: {now:%Y-%m-%d %H:%M} 周{_WEEKDAYS[now.weekday()]}\n"
        f"group: {where}\n"
        f"speaker: {who}\n"
        f"self: {me}\n"
        "</ctx>"
    )


def build_system_prompt(
    cfg: Config,
    persona_text: str,
    *,
    extra_sections: list[str] | None = None,
) -> str:
    """只放静态内容：人设 + 规则 + 扩展注入。一变缓存就废，别在这里塞时间。"""
    blocks = [persona_text.strip(), RULES.strip()]
    for section in extra_sections or []:
        if section and section.strip():
            blocks.append(section.strip())
    return "\n\n".join(blocks)


def merge_adjacent(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只合并连续的同角色 *机器人* 消息。

    群聊里连续十几条不同人的发言如果被并成一个 user 轮次，模型会把最后说话的人
    当成这一大段的作者，出现“把 A 说的话算到 B 头上”。assistant 是自己的话，
    合并无害；user 一律保留独立轮次。
    """
    merged: list[dict[str, Any]] = []
    for msg in messages:
        if (
            merged
            and merged[-1]["role"] == msg["role"] == "assistant"
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
