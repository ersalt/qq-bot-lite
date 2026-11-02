"""图片下载 / 缩放 / 转 data URL。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import logging
import os
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from ..config import ImagesConfig
from ..onebot.protocol import ImageRef

log = logging.getLogger(__name__)


class ImageLoader:
    def __init__(self, cfg: ImagesConfig):
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.timeout, connect=5.0),
            follow_redirects=True,
            trust_env=cfg.trust_env,
            # QQ 图床对裸请求有时直接 403，带个 UA 和 referer 更稳。
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
                "Referer": "https://q.qq.com/",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def build_content(self, text: str, refs: list[ImageRef]) -> str | list[dict]:
        """有图就返回 OpenAI 多模态数组，没图（或全失败）返回纯文本。"""
        if not self.cfg.enabled or not refs:
            return text

        data_urls: list[str] = []
        for ref in refs[: self.cfg.max_per_message]:
            raw = await self._fetch(ref)
            if not raw:
                continue
            try:
                data_url = await asyncio.to_thread(_encode_jpeg, raw, self.cfg.max_side)
            except Exception:  # noqa: BLE001 - 坏图不能拖垮回复
                log.warning("图片解码失败: %s", ref.source, exc_info=True)
                continue
            data_urls.append(data_url)

        if not data_urls:
            note = "（对方发了图片，但没能加载出来）"
            return f"{text}\n{note}" if text else note

        parts: list[dict] = []
        if text:
            parts.append({"type": "text", "text": text})
        else:
            parts.append({"type": "text", "text": "（对方发来图片）"})
        for url in data_urls:
            parts.append({"type": "image_url", "image_url": {"url": url}})
        log.info("已附带 %d 张图片", len(data_urls))
        return parts

    async def _fetch(self, ref: ImageRef) -> bytes | None:
        source = ref.source
        if not source:
            return None
        try:
            if source.startswith("base64://"):
                raw = base64.b64decode(source[9:])
            elif source.startswith("file://"):
                raw = Path(source[7:]).read_bytes()
            elif source.startswith(("http://", "https://")):
                resp = await self._client.get(source)
                resp.raise_for_status()
                raw = resp.content
            elif os.path.exists(source):
                raw = await asyncio.to_thread(Path(source).read_bytes)
            else:
                log.warning("无法识别的图片地址: %.120s", source)
                return None
        except (httpx.HTTPError, OSError, binascii.Error) as exc:
            log.warning("图片获取失败 %s: %s", source[:80], exc)
            return None

        if len(raw) > self.cfg.max_bytes:
            log.warning("图片超过 %d 字节上限，跳过: %.120s", self.cfg.max_bytes, source)
            return None
        return raw


def _encode_jpeg(raw: bytes, max_side: int) -> str:
    with Image.open(io.BytesIO(raw)) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side), Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
