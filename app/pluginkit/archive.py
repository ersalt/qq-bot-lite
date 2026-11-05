"""插件 ZIP 安装：支持带/不带顶层插件目录两种结构，先验后解压，避免 zip slip。"""

from __future__ import annotations

import io
import re
import stat
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from . import META_FILE
from .manifest import read_manifest, valid_name

MAX_ZIP = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILES = 1000
_JUNK_NAMES = {".DS_Store", "Thumbs.db"}


class InstallError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _check_entry(info: zipfile.ZipInfo, total: int) -> int:
    name = info.filename
    if "\x00" in name:
        raise InstallError("ZIP 里有非法文件名")
    if "\\" in name:
        raise InstallError("ZIP 里用了反斜杠路径")
    if ":" in name:
        raise InstallError(f"ZIP 里有非法路径：{name}")
    pure = PurePosixPath(name)
    if not pure.parts:
        raise InstallError("ZIP 里有空文件名")
    if pure.is_absolute() or any(part in {"..", ""} for part in pure.parts):
        raise InstallError(f"ZIP 里有越界路径：{name}")
    if info.is_dir():
        return total
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise InstallError(f"ZIP 里有符号链接：{name}")
    if info.flag_bits & 0x1:
        raise InstallError("不支持加密 ZIP")
    total += info.file_size
    if total > MAX_TOTAL:
        raise InstallError("ZIP 解压后超过 64MB 上限")
    return total


def _is_junk(pure: PurePosixPath) -> bool:
    """macOS/Windows 打包时常见的隐藏垃圾，不该让整个包失败。"""
    return pure.parts[0] == "__MACOSX" or pure.name in _JUNK_NAMES


def _id_from_hint(hint: str | None) -> str | None:
    """根目录布局没有文件夹名，用 ZIP 文件名/name 参数当插件 id。"""
    if not hint:
        return None
    raw = unquote(hint).strip().replace("\\", "/").rsplit("/", 1)[-1]
    if raw.lower().endswith(".zip"):
        raw = raw[:-4]
    candidate = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    return candidate if valid_name(candidate) else None


def _declared_id(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str | None:
    """根目录布局先读 plugin.toml 里的 id；它比 ZIP 文件名更权威。"""
    if info.file_size > 256 * 1024:
        raise InstallError("plugin.toml 超过 256KB，拒绝安装")
    try:
        payload = archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise InstallError(f"读取 plugin.toml 失败：{exc}") from exc
    if len(payload) > 256 * 1024:
        raise InstallError("plugin.toml 超过 256KB，拒绝安装")
    try:
        data = tomllib.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise InstallError(f"plugin.toml 读不了：{exc}") from exc
    value = data.get("id")
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    if not valid_name(value):
        raise InstallError(f"plugin.toml 的 id 不合法：{value}")
    return value


def install_zip(data: bytes, plugins_root: Path, name_hint: str | None = None) -> str:
    """校验并安装一个插件 ZIP，返回插件 id。

    接受两种结构：

    1. `<插件id>/plugin.toml`（所有文件都在同一个顶层目录里）
    2. `plugin.toml` 直接在 ZIP 根目录（插件 id 取 ZIP 文件名/name 参数）

    先完整校验再解压到 plugins_root 下的隐藏临时目录，清单有效后才 rename 成正式目录。
    解压时按实际写出的字节再卡一次大小。
    """
    if not data:
        raise InstallError("ZIP 是空的")
    if len(data) > MAX_ZIP:
        raise InstallError(f"ZIP 超过 {MAX_ZIP // 1048576}MB 上限", 413)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise InstallError("不是有效的 ZIP 文件") from exc

    with archive:
        infos = archive.infolist()
        if not infos:
            raise InstallError("ZIP 是空的")
        if len(infos) > MAX_FILES:
            raise InstallError(f"ZIP 文件数超过 {MAX_FILES} 上限")
        total = 0
        for info in infos:
            total = _check_entry(info, total)

        files: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        for info in infos:
            if info.is_dir():
                continue
            pure = PurePosixPath(info.filename)
            if _is_junk(pure):
                continue
            files.append((info, pure))
        if not files:
            raise InstallError("ZIP 里没有文件")

        paths = [pure for _, pure in files]
        names = {pure.as_posix() for pure in paths}
        top_dirs = {pure.parts[0] for pure in paths}
        folder_meta = [name for name in top_dirs if f"{name}/{META_FILE}" in names]
        has_root_meta = META_FILE in names
        has_root_level_files = any(len(pure.parts) == 1 for pure in paths)

        strip_prefix = 0
        if has_root_meta:
            if folder_meta:
                raise InstallError("ZIP 同时有根目录和文件夹里的 plugin.toml，结构不明确")
            meta_info = next(
                info for info, pure in files if pure.as_posix() == META_FILE
            )
            root = _declared_id(archive, meta_info) or _id_from_hint(name_hint)
            if root is None:
                raise InstallError(
                    "根目录布局需要把 ZIP 命名为 <插件id>.zip（或加 ?name=<插件id>）"
                )
        elif len(folder_meta) == 1 and len(top_dirs) == 1 and not has_root_level_files:
            root = folder_meta[0]
            if not valid_name(root):
                raise InstallError(f"插件目录名不合法：{root}")
            strip_prefix = 1
        elif folder_meta:
            raise InstallError("ZIP 必须只有一个插件目录，且所有文件都放在里面")
        else:
            raise InstallError("ZIP 根目录或顶层文件夹里缺少 plugin.toml")

        dest = plugins_root / root
        if dest.exists():
            raise InstallError(f"插件「{root}」已存在，先删除再装", 409)

        plugins_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=plugins_root, prefix=".install-") as temp:
            temp_path = Path(temp)
            try:
                for info, pure in files:
                    relative = PurePosixPath(*pure.parts[strip_prefix:])
                    if not relative.parts:
                        continue
                    target = temp_path / root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    written = 0
                    with archive.open(info) as src, target.open("wb") as dst:
                        while True:
                            chunk = src.read(64 * 1024)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > MAX_TOTAL:
                                raise InstallError("ZIP 解压后超过 64MB 上限")
                            dst.write(chunk)
            except InstallError:
                raise
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise InstallError(f"解压失败：{exc}") from exc

            manifest = read_manifest(temp_path, root)
            if manifest.error:
                raise InstallError(f"插件清单无效：{manifest.error}")
            try:
                (temp_path / root).rename(dest)
            except OSError as exc:
                raise InstallError(f"安装失败：{exc}") from exc
    return root
