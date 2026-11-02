"""轻量 token 估算：不引入 tiktoken，避免联网下载词表。

中文按 1 字 ≈ 1 token，英文/符号按 3.5 字符 ≈ 1 token 粗算，误差对“预算裁剪”足够。
"""

from __future__ import annotations

import math
import re
from typing import Any

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    rest = max(len(text) - cjk, 0)
    return cjk + math.ceil(rest / 3.5)


def estimate_message_tokens(content: Any) -> int:
    """content 可以是字符串，也可以是 OpenAI 多模态数组。"""
    if isinstance(content, str):
        return estimate_text_tokens(content)
    if isinstance(content, list):
        total = 4  # 结构开销
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                total += estimate_text_tokens(str(part.get("text", "")))
            elif part.get("type") == "image_url":
                # 一张缩到 1024 的图，Qwen 系视觉编码器大约 700~1000 token，取中位估计。
                total += 850
        return total
    return 0


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(msg.get("content", "")) + 4 for msg in messages)
