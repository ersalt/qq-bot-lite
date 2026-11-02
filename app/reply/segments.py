"""流式文本 → 多条消息的切分器。

目标：首条尽快发出去，同时不把一句话切断。
"""

from __future__ import annotations

SENTENCE_END = "。！？!?…\n"
SOFT_END = "，,；;、"


class Segmenter:
    def __init__(
        self,
        max_chars: int = 120,
        max_segments: int = 4,
        min_first: int = 6,
        min_next: int = 12,
    ):
        self.max_chars = max(max_chars, 16)
        self.max_segments = max(max_segments, 1)
        self.min_first = min_first
        self.min_next = min_next
        self._buf = ""
        self.sent = 0

    @property
    def min_len(self) -> int:
        return self.min_first if self.sent == 0 else self.min_next

    def feed(self, chunk: str) -> list[str]:
        if not chunk:
            return []
        self._buf += chunk
        out: list[str] = []
        while self.sent < self.max_segments - 1:
            index = self._cut_index()
            if index <= 0:
                break
            piece = self._buf[:index].strip()
            self._buf = self._buf[index:]
            if piece:
                out.append(piece)
                self.sent += 1
        return out

    def flush(self) -> list[str]:
        """把剩余文本吐完。超过单条上限时继续按句读/空格切开。"""
        out: list[str] = []
        while self._buf:
            text = self._buf.strip()
            if not text:
                self._buf = ""
                break
            if len(text) <= self.max_chars:
                out.append(text)
                self._buf = ""
                break
            index = self._hard_cut(text)
            out.append(text[:index].strip())
            self._buf = text[index:]
        return [piece for piece in out if piece]

    # ---------- 内部 ----------

    def _cut_index(self) -> int:
        buf = self._buf
        lo = self.min_len
        if len(buf) < lo:
            return -1

        window = buf[: self.max_chars]

        newline = window.find("\n")
        if newline >= 0 and newline + 1 >= lo:
            return newline + 1

        best = -1
        for index, char in enumerate(window):
            if char in SENTENCE_END and index + 1 >= lo:
                best = index
        if best >= 0:
            return best + 1

        if len(buf) >= self.max_chars:
            for index in range(len(window) - 1, lo - 1, -1):
                if buf[index] in SOFT_END or buf[index] == " ":
                    return index + 1
            return len(window)
        return -1

    def _hard_cut(self, text: str) -> int:
        window = text[: self.max_chars]
        for index in range(len(window) - 1, max(self.max_chars // 2, 1) - 1, -1):
            if text[index] in SENTENCE_END or text[index] in SOFT_END or text[index] == " ":
                return index + 1
        return self.max_chars
