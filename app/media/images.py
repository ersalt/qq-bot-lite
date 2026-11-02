"""图片下载 / 缩放 / 转 data URL。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from ..config import ImagesConfig
from ..onebot.protocol import ImageRef

log = logging.getLogger(__name__)

# 本来就够小、格式又是通行的，就没必要重编码（带字的截图尤其怕 JPEG）。
_PASSTHROUGH = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


@dataclass(slots=True)
class PreparedImage:
    """下载并归一化过的图：data_url 直接进请求，path 用来写进历史。"""

    path: Path
    data_url: str


class ImageLoader:
    def __init__(self, cfg: ImagesConfig, cache_dir: Path | None = None):
        self.cfg = cfg
        self.cache_dir = cache_dir or (
            Path(__file__).resolve().parent.parent.parent / "data" / "images"
        )
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

    async def prepare(self, refs: list[ImageRef]) -> list["PreparedImage"]:
        """下载 → 归一化 → 落盘缓存，返回可以直接复用的图。

        落盘是为了历史：QQ 的图链会过期，只有存下来的副本才能在下一轮
        “你刚才那张图里有什么”时重新塞给模型。
        """
        if not self.cfg.enabled or not refs:
            return []
        prepared: list[PreparedImage] = []
        for ref in refs[: self.cfg.max_per_message]:
            raw = await self._fetch(ref)
            if not raw:
                continue
            try:
                prepared.append(await asyncio.to_thread(self._store, raw))
            except Exception:  # noqa: BLE001 - 坏图不能拖垮回复
                log.warning("图片解码失败: %s", ref.source, exc_info=True)
        return prepared

    def _store(self, raw: bytes) -> "PreparedImage":
        """归一化后存成文件，同名同内容复用。"""
        data_url = _prepare(raw, self.cfg.max_side)
        header, payload = data_url.split(",", 1)
        binary = base64.b64decode(payload)
        digest = hashlib.sha1(binary).hexdigest()[:16]
        suffix = ".png" if "png" in header else ".jpg"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{digest}{suffix}"
        if not path.exists():
            path.write_bytes(binary)
        return PreparedImage(path=path, data_url=data_url)

    @staticmethod
    def load_image(path: Path) -> str | None:
        """把历史里的图重新编成 data URL。"""
        try:
            binary = path.read_bytes()
        except OSError:
            return None
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(binary).decode("ascii")

    async def build_content(self, text: str, images: list["PreparedImage"]) -> str | list[dict]:
        """有图就返回 OpenAI 多模态数组，没图（或全失败）返回纯文本。"""
        if not self.cfg.enabled or not images:
            return text

        parts: list[dict] = []
        if text:
            parts.append({"type": "text", "text": text})
        else:
            parts.append({"type": "text", "text": "（对方发来图片）"})
        for image in images:
            if image.data_url:
                parts.append({"type": "image_url", "image_url": {"url": image.data_url}})
        log.info("已附带 %d 张图片", len(images))
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
            log.warning(
                "图片 %.1fMB 超过 %.1fMB 上限，跳过: %.100s",
                len(raw) / 1048576,
                self.cfg.max_bytes / 1048576,
                source,
            )
            return None
        log.debug("图片下载完成 %.1fMB: %.100s", len(raw) / 1048576, source)
        return raw


def _prepare(raw: bytes, max_side: int) -> str:
    """小图原样送，大图才缩。

    截图这类带字的图，重编码成 JPEG q85 会把笔画糊掉；AstrBot 也是“只在超过
    1MB 时压缩”。所以：本来就不大、尺寸也没超限、格式又是浏览器/模型都认的，
    直接把原始字节转 base64 发过去。
    """
    with Image.open(io.BytesIO(raw)) as img:
        fmt = (img.format or "").upper()
        if len(raw) <= 1_000_000 and max(img.size) <= max_side and fmt in _PASSTHROUGH:
            mime = _PASSTHROUGH[fmt]
            return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
        return _encode_jpeg(img, max_side)


def _encode_jpeg(img: "Image.Image", max_side: int) -> str:
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
