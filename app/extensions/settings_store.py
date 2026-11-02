"""插件自己的配置：`config/plugins.toml`，一个插件一个 section。

和主配置一样可以手改，所以写回时保留文件头注释；每个插件段整段重写，
因为它的键集合由插件清单决定，不是用户手写的顺序。
"""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

HEADER = """# 插件配置。由设置面板写入，也可以手动编辑。
# 每个插件一个 [section]，键来自插件 MANIFEST 里的 config 声明。
"""


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:g}"
        return text if "." in text else f"{text}.0"
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(str(item), ensure_ascii=False) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


class PluginSettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        try:
            with self.path.open("rb") as handle:
                raw = tomllib.load(handle)
            self._data = {key: dict(value) for key, value in raw.items() if isinstance(value, dict)}
        except (OSError, tomllib.TOMLDecodeError) as exc:
            log.error("读取插件配置失败 %s: %s", self.path, exc)
            self._data = {}

    def of(self, name: str, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(defaults or {})
        merged.update(self._data.get(name, {}))
        return merged

    def save(self, name: str, values: dict[str, Any]) -> None:
        self._data[name] = dict(values)
        self._write()

    def _write(self) -> None:
        lines = [HEADER.rstrip("\n")]
        for name in sorted(self._data):
            lines.append("")
            lines.append(f"[{name}]")
            for key in sorted(self._data[name]):
                lines.append(f"{key} = {_fmt(self._data[name][key])}")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            log.error("写入插件配置失败 %s: %s", self.path, exc)


def defaults_from(config: list[dict[str, Any]]) -> dict[str, Any]:
    """从 MANIFEST 的 config 声明推默认值。"""
    values: dict[str, Any] = {}
    for field in config:
        key = field.get("key")
        if not key:
            continue
        if "default" in field:
            values[key] = field["default"]
        elif field.get("type") == "number":
            values[key] = field.get("min", 0)
        elif field.get("type") == "bool":
            values[key] = bool(field.get("default", False))
        else:
            values[key] = ""
    return values
