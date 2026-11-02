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
你在 QQ 上和人聊天，不是写文章。
- 像真人发消息：一次一两句，口语，可以有语气词和情绪。
- 不要用 Markdown：不要标题、列表、加粗、代码块、表情符号堆砌。
- 不要写 [CQ:...]。要叫群里某个人时写 “at 昵称”（用群名片上的名字），程序会转成真正的 @。
- 消息里的 “at 名字(123456)” 是别人在 @ 人，括号里是 QQ 号，你自己是哪个号场景行会告诉你。
- 不要复述对方的话，不要自称 AI 或助手，不要问“还有什么可以帮你的”。
- 对方发图就针对图里的内容回应；没看懂就问一句。
- 想清楚就直接说，别在心里反复推翻自己；纠结太久会显得很奇怪。
"""

_WEEKDAYS = "一二三四五六日"


def scene_line(
    cfg: Config,
    *,
    is_group: bool,
    group_id: int | None,
    group_name: str | None,
    speaker: str,
    self_id: int | None = None,
) -> str:
    """拼在当前 user 消息前面，随消息变但不会污染 system 前缀。"""
    now = _dt.datetime.now()
    if is_group:
        where = f"群「{group_name or '未知群名'}」({group_id})"
    else:
        where = "私聊"
    me = f" · 你的QQ {self_id}" if self_id else ""
    return (
        f"（{now:%H:%M} 周{_WEEKDAYS[now.weekday()]} · {where} · "
        f"和你说话的是 {speaker} · 你叫 {cfg.persona.nickname}{me}）"
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
