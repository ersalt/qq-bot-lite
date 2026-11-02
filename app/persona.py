"""人设加载。"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

DEFAULT_PERSONA = """\
你叫{name}，一个待在群里聊天的普通女生，不是助手、不是客服。
说话简短、口语化，有自己的情绪和小脾气，偶尔吐槽。
不用敬语，不总结，不列点，不说“作为AI”。
"""


class Persona:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._text = ""
        self._mtime = 0.0
        self.reload()

    def reload(self) -> None:
        path: Path = self.cfg.persona_path
        if path.exists():
            self._text = path.read_text(encoding="utf-8").strip()
            self._mtime = path.stat().st_mtime
            log.info("已加载人设: %s (%d 字)", path, len(self._text))
        else:
            self._text = DEFAULT_PERSONA.format(name=self.cfg.persona.nickname)
            log.warning("人设文件不存在，使用内置默认人设: %s", path)

    @property
    def text(self) -> str:
        path = self.cfg.persona_path
        try:
            if path.exists() and path.stat().st_mtime > self._mtime:
                self.reload()
        except OSError:
            pass
        return self._text

    @property
    def name(self) -> str:
        return self.cfg.persona.nickname
