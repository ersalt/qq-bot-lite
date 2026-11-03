"""OneBot 11 消息段解析与构造。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_CQ_RE = re.compile(r"\[CQ:([A-Za-z0-9_.\-]+)((?:,[^\[\]]*)?)\]")
_CQ_ESCAPES = {
    "&#91;": "[",
    "&#93;": "]",
    "&#44;": ",",
    "&amp;": "&",
}

# 这些段在文本里没有语义，直接跳过。
_SKIP_TYPES = {"face", "mface", "rps", "dice", "shake", "poke", "json", "xml", "markdown"}


def _cq_unescape(text: str) -> str:
    for src, dst in _CQ_ESCAPES.items():
        text = text.replace(src, dst)
    return text


def _parse_cq_params(raw: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in raw.strip(",").split(","):
        if not item or "=" not in item:
            continue
        key, _, value = item.partition("=")
        params[key] = _cq_unescape(value)
    return params


@dataclass(slots=True)
class ImageRef:
    url: str | None = None
    file: str | None = None
    summary: str | None = None

    @property
    def source(self) -> str | None:
        return self.url or self.file


@dataclass(slots=True)
class ParsedContent:
    text: str = ""
    images: list[ImageRef] = field(default_factory=list)
    at_targets: list[str] = field(default_factory=list)
    reply_to: str | None = None
    has_face: bool = False
    # 整条消息只有 @、没有别的内容（“只 @ 了一下”）
    only_mentions: bool = False


def normalize_segments(message: Any) -> list[dict[str, Any]]:
    """把 array / string 两种消息格式统一成段列表。"""
    if isinstance(message, list):
        return [seg for seg in message if isinstance(seg, dict) and seg.get("type")]
    if isinstance(message, str):
        return _cq_to_segments(message)
    return []


def _cq_to_segments(text: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    pos = 0
    for match in _CQ_RE.finditer(text):
        if match.start() > pos:
            segments.append({"type": "text", "data": {"text": _cq_unescape(text[pos : match.start()])}})
        segments.append({"type": match.group(1), "data": _parse_cq_params(match.group(2))})
        pos = match.end()
    if pos < len(text):
        segments.append({"type": "text", "data": {"text": _cq_unescape(text[pos:])}})
    return segments


def parse_content(message: Any) -> ParsedContent:
    content = ParsedContent()
    parts: list[str] = []
    has_other = False
    for seg in normalize_segments(message):
        seg_type = seg.get("type", "")
        data = seg.get("data") or {}
        if seg_type == "text":
            text = str(data.get("text", ""))
            if text.strip():
                has_other = True
            parts.append(text)
        elif seg_type == "at":
            qq = str(data.get("qq", ""))
            if qq:
                content.at_targets.append(qq)
                if qq == "all":
                    parts.append(" at 全体成员")
                else:
                    name = data.get("name") or data.get("card")
                    # 用 "at" 而不是 "@"：模型会把 @ 当成普通符号照抄，
                    # 前后都写 at 才能让“谁在叫谁”这件事无歧义。
                    parts.append(f" at {name}({qq})" if name else f" at {qq}")
        elif seg_type == "image":
            source_url = data.get("url") or data.get("file")
            if source_url:
                has_other = True
                content.images.append(
                    ImageRef(
                        url=data.get("url"),
                        file=data.get("file"),
                        summary=data.get("summary") or data.get("sub_type"),
                    )
                )
        elif seg_type == "reply":
            reply_id = data.get("id")
            if reply_id is not None:
                content.reply_to = str(reply_id)
        elif seg_type in _SKIP_TYPES:
            content.has_face = content.has_face or seg_type in {"face", "mface"}
        # 其它未知段（forward、file 等）忽略，避免污染上下文。

    content.text = " ".join("".join(parts).split())
    content.only_mentions = bool(content.at_targets) and not has_other
    return content


def text_segment(text: str) -> dict[str, Any]:
    return {"type": "text", "data": {"text": text}}


def at_segment(qq: int | str) -> dict[str, Any]:
    return {"type": "at", "data": {"qq": str(qq)}}


def reply_segment(message_id: int | str) -> dict[str, Any]:
    return {"type": "reply", "data": {"id": str(message_id)}}
