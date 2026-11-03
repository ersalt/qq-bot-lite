"""插件自己的设置：`data/plugin_data/<名字>/settings.toml`。

和插件的数据放一起，一个插件一个文件；键由 plugin.toml 里的 config 声明决定，
所以整份重写没有心理负担。
"""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

HEADER = """# 这个插件的设置，由设置面板写入，也可以手改。
# 键来自插件自己的 plugin.toml 里 [[config]] 的声明。
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


def defaults_from(config: list[dict[str, Any]]) -> dict[str, Any]:
    """从 plugin.toml 的 [[config]] 推默认值。"""
    values: dict[str, Any] = {}
    for field in config:
        key = field.get("key")
        if not key:
            continue
        kind = field.get("type", "text")
        if "default" in field:
            values[key] = field["default"]
        elif kind == "number":
            values[key] = field.get("min", 0)
        elif kind == "bool":
            values[key] = False
        else:
            values[key] = ""
    return values


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return dict(tomllib.load(handle))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.error("读取插件设置失败 %s: %s", path, exc)
        return {}


def save(path: Path, values: dict[str, Any]) -> None:
    lines = [HEADER.rstrip("\n")]
    for key in sorted(values):
        lines.append(f"{key} = {_fmt(values[key])}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        log.error("写入插件设置失败 %s: %s", path, exc)
