"""配置的读取、校验、写回与热应用。

写回是“逐行改”而不是整份重写：config.toml 里的注释是给人看的，
重写一次就全没了。所以只在对应 section 里找那一行替换；
找不到就替换掉同名注释行；再找不到才追加到 section 末尾。
"""

from __future__ import annotations

import json
import logging
import re
import types
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from ..config import (
    Config,
    ContextConfig,
    ExtensionsConfig,
    ImagesConfig,
    LLMConfig,
    OneBotConfig,
    PersonaConfig,
    ReplyConfig,
    ServerConfig,
    TriggerConfig,
)

log = logging.getLogger(__name__)

SECTION_TYPES = {
    "server": ServerConfig,
    "onebot": OneBotConfig,
    "llm": LLMConfig,
    "context": ContextConfig,
    "persona": PersonaConfig,
    "trigger": TriggerConfig,
    "reply": ReplyConfig,
    "images": ImagesConfig,
    "extensions": ExtensionsConfig,
}

# 这些字段在启动时被组件抓走了，改了只能重启进程。
RESTART_FIELDS = {
    "server.host",
    "server.port",
    "server.status_interval",
    "onebot.path",
    "onebot.access_token",
    "onebot.max_connections",
    "images.timeout",
    "images.trust_env",
}


def _section_of(cfg: Config, name: str) -> Any:
    return getattr(cfg, name)


def _field_type(section: str, field: str) -> Any:
    hints = get_type_hints(SECTION_TYPES[section])
    return hints[field]


def _is_optional(tp: Any) -> bool:
    return get_origin(tp) is types.UnionType and type(None) in get_args(tp)


def _base_type(tp: Any) -> Any:
    if _is_optional(tp):
        return next(a for a in get_args(tp) if a is not type(None))
    return tp


def collect_values(cfg: Config) -> dict[str, Any]:
    """按 schema 的 key 取出当前值，扁平化。"""
    from .schema import SECTIONS

    out: dict[str, Any] = {}
    for section in SECTIONS:
        name = section["id"]
        holder = _section_of(cfg, name)
        for field in section["fields"]:
            key = field["key"]
            attr = key.split(".", 1)[1]
            out[key] = getattr(holder, attr)
    return out


def _coerce(key: str, value: Any) -> Any:
    """按 dataclass 的注解把前端传来的值转成正确的类型。"""
    section, _, field = key.partition(".")
    if section not in SECTION_TYPES:
        raise ValueError(f"未知配置段 {section}")
    tp = _field_type(section, field)
    optional = _is_optional(tp)
    base = _base_type(tp)

    if value is None or value == "":
        if optional:
            return None
        if base is str:
            return ""
        if base is bool:
            return False
        if base in (int, float):
            return 0
        if get_origin(base) is list:
            return []
        return None

    if base is bool:
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("1", "true", "yes", "on")

    if base is int:
        return int(float(value))

    if base is float:
        return float(value)

    if base is str:
        return str(value)

    if get_origin(base) is list:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [part.strip() for part in str(value).split(",") if part.strip()]

    return value


def normalize(updates: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """转类型 + 检查取值范围，返回 (干净的值, 错误列表)。"""
    from .schema import SECTIONS

    limits: dict[str, dict[str, Any]] = {}
    labels: dict[str, str] = {}
    for section in SECTIONS:
        for field in section["fields"]:
            limits[field["key"]] = field
            labels[field["key"]] = field["label"]

    clean: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    for key, raw in updates.items():
        if key not in limits:
            continue
        field = limits[key]
        try:
            value = _coerce(key, raw)
        except (TypeError, ValueError):
            errors.append({"field": key, "message": f"{labels[key]}：值不是合法数字"})
            continue

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            low, high = field.get("min"), field.get("max")
            if low is not None and value < low:
                errors.append({"field": key, "message": f"{labels[key]}：不能小于 {low}"})
                continue
            if high is not None and value > high:
                errors.append({"field": key, "message": f"{labels[key]}：不能大于 {high}"})
                continue
        if isinstance(value, str) and field.get("type") == "text" and not value.strip():
            if key in ("llm.model", "llm.base_url", "server.host", "onebot.path"):
                errors.append({"field": key, "message": f"{labels[key]}：不能为空"})
                continue
        clean[key] = value
    return clean, errors


# ---------- 写回 toml ----------


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


_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _set_line(text: str, section: str, name: str, value: Any) -> str:
    lines = text.splitlines()
    active = re.compile(rf"^(\s*){re.escape(name)}\s*=")
    commented = re.compile(rf"^(\s*)#\s*{re.escape(name)}\s*=(.*)$")

    current: str | None = None
    section_found = False
    section_end = 0
    replaced = False

    for index, line in enumerate(lines):
        match = _SECTION_RE.match(line)
        if match:
            current = match.group(1).strip()
            if current == section:
                section_found = True
                section_end = index
            continue
        if current != section or replaced:
            continue
        section_end = index
        if active.match(line) or commented.match(line):
            lines[index] = f"{name} = {_fmt(value)}"
            replaced = True

    if replaced:
        return "\n".join(lines) + "\n"

    if not section_found:
        # 整段都不存在：补一个 section 到文件末尾，别把键插到别的段里去。
        body = "\n".join(lines).rstrip("\n")
        return f'{body}\n\n[{section}]\n{name} = {_fmt(value)}\n'

    lines.insert(section_end + 1, f"{name} = {_fmt(value)}")
    return "\n".join(lines) + "\n"


def save_to_toml(path: Path, updates: dict[str, Any]) -> None:
    text = path.read_text(encoding="utf-8")
    for key, value in updates.items():
        section, _, name = key.partition(".")
        text = _set_line(text, section, name, value)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def apply_to_config(cfg: Config, updates: dict[str, Any]) -> None:
    """原地改，不换对象：ChatService / ReplySender 都拿着同一个 cfg 引用。"""
    for key, value in updates.items():
        section, _, field = key.partition(".")
        holder = _section_of(cfg, section)
        setattr(holder, field, value)


def restart_fields(updates: dict[str, Any]) -> list[str]:
    from .schema import SECTIONS

    labels = {
        field["key"]: f"{section['title']} · {field['label']}"
        for section in SECTIONS
        for field in section["fields"]
    }
    return [labels.get(key, key) for key in updates if key in RESTART_FIELDS]

