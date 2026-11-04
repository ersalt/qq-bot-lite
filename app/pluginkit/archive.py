"""插件 ZIP 安装：只接受单个插件顶层目录，先验后解压，避免 zip slip。"""

from __future__ import annotations

import io
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .manifest import read_manifest, valid_name

MAX_ZIP = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILES = 1000


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
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"..", ""} for part in pure.parts):
        raise InstallError(f"ZIP 里有越界路径：{name}")
    if info.is_dir():
        return total
    if len(pure.parts) < 2:
        raise InstallError("ZIP 必须只有一个顶层插件目录（<插件id>/plugin.toml）")
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise InstallError(f"ZIP 里有符号链接：{name}")
    if info.flag_bits & 0x1:
        raise InstallError("不支持加密 ZIP")
    total += info.file_size
    if total > MAX_TOTAL:
        raise InstallError("ZIP 解压后超过 64MB 上限")
    return total


def install_zip(data: bytes, plugins_root: Path) -> str:
    """校验并安装一个插件 ZIP，返回插件 id。

    只接受 `<id>/plugin.toml` 这一种结构；先完整校验再解压到 plugins_root 下的隐藏临时
    目录，清单有效后才 rename 成正式目录。解压时按实际写出的字节再卡一次大小。
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
        roots: set[str] = set()
        total = 0
        for info in infos:
            total = _check_entry(info, total)
            if not info.is_dir():
                roots.add(PurePosixPath(info.filename).parts[0])
        if len(roots) != 1:
            raise InstallError("ZIP 必须只有一个顶层插件目录")
        root = roots.pop()
        if not valid_name(root):
            raise InstallError(f"插件目录名不合法：{root}")
        dest = plugins_root / root
        if dest.exists():
            raise InstallError(f"插件「{root}」已存在，先删除再装", 409)

        plugins_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=plugins_root, prefix=".install-") as temp:
            temp_path = Path(temp)
            try:
                for info in infos:
                    if info.is_dir():
                        continue
                    target = temp_path / PurePosixPath(info.filename)
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
