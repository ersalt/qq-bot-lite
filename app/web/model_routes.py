"""模型配置管理：多套配置分开存，切换时写回 [llm] 和上下文预算。

- `config/models.toml` 存所有配置（一个 `[[profile]]` 一张卡）
- `config/config.toml` 的 `[llm]` 和 `context.max_messages/max_tokens` 始终是当前卡生效值
- 切换 = 把卡里的模型参数和上下文预算抄进 config + 热应用 + 重建 http 客户端
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..config import Config, ContextConfig, LLMConfig
from ..llm.client import LLMClient
from .settings import save_to_toml

log = logging.getLogger(__name__)

FIELDS = (
    "base_url",
    "api_key",
    "model",
    "max_context",
    "temperature",
    "top_p",
    "max_tokens",
    "reasoning_effort",
    "stream",
    "thinking_budget",
    "timeout",
    "max_concurrency",
    "presence_penalty",
    "frequency_penalty",
)

# 这两项属于“当前模型能看多少历史”，跟模型一起切；models.toml 里用
# context_max_* 避免和 llm.max_tokens 重名。
CONTEXT_FIELDS = ("context.max_messages", "context.max_tokens")
CONTEXT_SERIAL = {
    "context.max_messages": "context_max_messages",
    "context.max_tokens": "context_max_tokens",
}
ALL_PROFILE_KEYS = (*FIELDS, *CONTEXT_SERIAL.values())


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:g}"
        return text if "." in text else f"{text}.0"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.error("读取模型配置失败 %s: %s", path, exc)
        return []
    items = raw.get("profile") or []
    return [dict(item) for item in items if isinstance(item, dict) and item.get("name")]


TEMPLATE_NAME = "default"


def _defaults() -> dict[str, Any]:
    """每张卡都必须能独立落地的字段；缺字段时用 dataclass 默认值补齐。"""
    llm_default = LLMConfig()
    context_default = ContextConfig()
    data = {
        key: getattr(llm_default, key)
        for key in FIELDS
        if getattr(llm_default, key) is not None
    }
    data[CONTEXT_SERIAL["context.max_messages"]] = context_default.max_messages
    data[CONTEXT_SERIAL["context.max_tokens"]] = context_default.max_tokens
    return data


def _write(path: Path, profiles: list[dict[str, Any]]) -> None:
    lines = ["# 模型配置。每张 [[profile]] 是一套完整参数，切换时会抄进 config.toml 的 [llm]。"]
    for profile in profiles:
        lines.append("")
        lines.append("[[profile]]")
        lines.append(f'name = {_fmt(profile["name"])}')
        if profile.get("template"):
            lines.append("template = true")
        for key in FIELDS:
            value = profile.get(key)
            if value is not None:
                lines.append(f"{key} = {_fmt(value)}")
        for key in CONTEXT_FIELDS:
            # snapshot/前端用 context.max_messages，落盘用 context_max_messages；
            # 两种都接受，旧卡和新卡都能读。
            value = profile.get(CONTEXT_SERIAL[key], profile.get(key))
            if value is not None:
                lines.append(f"{CONTEXT_SERIAL[key]} = {_fmt(value)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _snapshot(cfg: Config) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key in FIELDS:
        value = getattr(cfg.llm, key, None)
        if value is not None:
            data[key] = value
    data["context.max_messages"] = cfg.context.max_messages
    data["context.max_tokens"] = cfg.context.max_tokens
    return data


def mount_model_api(app: FastAPI, cfg: Config, llm: LLMClient, config_path: Path) -> None:
    path = (config_path.parent) / "models.toml"

    if not path.exists():
        # 第一次跑：把当前 [llm] 存成一张卡，侧栏不至于空着。
        seed_name = "默认"
        _write(path, [{"name": seed_name, **_snapshot(cfg)}])
        cfg.llm.profile = seed_name
        try:
            save_to_toml(config_path, {"llm.profile": seed_name})
        except OSError:
            log.warning("写回 llm.profile 失败", exc_info=True)

    def normalize_existing() -> None:
        """一次性补齐旧卡片缺失的字段，之后保存单卡不会重写别的卡。"""
        defaults = _defaults()
        profiles = _read(path)
        changed = False
        for profile in profiles:
            for key, value in defaults.items():
                if key not in profile:
                    profile[key] = value
                    changed = True
        if changed:
            _write(path, profiles)

    normalize_existing()

    def template() -> dict[str, Any]:
        """隐藏的模板：新建配置照它抄，界面上不出现、也不能选中或删除。"""
        profiles = _read(path)
        found = next((item for item in profiles if item.get("template")), None)
        if found is not None:
            return found
        seed = {"name": TEMPLATE_NAME, "template": True, **_snapshot(cfg)}
        _write(path, [seed, *profiles])
        return seed

    template()  # 启动时就把隐藏模板补上，新建时才有东西可抄

    def items() -> list[dict[str, Any]]:
        defaults = _defaults()
        result = []
        for profile in _read(path):
            if profile.get("template"):
                continue  # 模板只在后台用，不出现在列表里
            merged = {**defaults, **profile}
            result.append(
                {
                    "name": profile["name"],
                    "model": merged.get("model", ""),
                    "base_url": merged.get("base_url", ""),
                    "max_context": merged.get("max_context", 0),
                    "active": profile["name"] == getattr(cfg.llm, "profile", ""),
                }
            )
        return result

    def save_all(profiles: list[dict[str, Any]]) -> None:
        _write(path, profiles)

    def apply(profile: dict[str, Any]) -> None:
        defaults = _defaults()
        updates = {}
        # 关键：缺字段也要显式回到默认值，不能保留上一张卡的值。
        for key in FIELDS:
            value = profile.get(key, defaults.get(key))
            if value is None:
                setattr(cfg.llm, key, None)
                updates[f"llm.{key}"] = None
                continue
            if key in ("temperature", "top_p"):
                value = float(value)
            elif key in ("max_tokens", "thinking_budget", "timeout", "max_concurrency", "max_context"):
                value = int(value)
            elif key in ("presence_penalty", "frequency_penalty"):
                value = float(value)
            elif key == "stream":
                value = bool(value)
            setattr(cfg.llm, key, value)
            updates[f"llm.{key}"] = value
        for key in CONTEXT_FIELDS:
            serial = CONTEXT_SERIAL[key]
            value = profile.get(serial, profile.get(key, defaults.get(serial)))
            if value is not None:
                value = int(value)
            setattr(cfg.context, key.split(".", 1)[1], value)
            updates[key] = value
        cfg.llm.profile = str(profile["name"])
        updates["llm.profile"] = str(profile["name"])
        save_to_toml(config_path, updates)

    @app.get("/api/models")
    async def list_models() -> JSONResponse:
        return JSONResponse(
            {"items": items(), "active": getattr(cfg.llm, "profile", ""), "values": _snapshot(cfg)}
        )

    @app.post("/api/models")
    async def manage(request: Request) -> JSONResponse:
        try:
            body: dict[str, Any] = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请求体不是 JSON") from exc
        action = str(body.get("action") or "").strip()
        name = str(body.get("name") or "").strip()
        profiles = _read(path)
        names = [item["name"] for item in profiles]

        if action == "activate":
            if name == TEMPLATE_NAME and any(item.get("template") and item["name"] == name for item in profiles):
                return JSONResponse({"ok": False, "message": "模板不能切换"}, status_code=400)
            if name not in names:
                return JSONResponse({"ok": False, "message": "配置不存在"}, status_code=404)
            profile = next(item for item in profiles if item["name"] == name)
            apply(profile)
            await llm.reload()
            log.info("切换模型配置: %s", name)
            return JSONResponse({"ok": True, "items": items(), "active": name, "values": _snapshot(cfg)})

        if action == "create":
            if not name:
                return JSONResponse({"ok": False, "message": "名字不能为空"}, status_code=400)
            if name in names:
                return JSONResponse({"ok": False, "message": "同名配置已存在"}, status_code=400)
            # 新配置照隐藏模板抄，不是抄当前正在用的那套。
            base = {
                key: value
                for key, value in template().items()
                if key in ALL_PROFILE_KEYS or key in CONTEXT_FIELDS
            }
            profiles.append({"name": name, **base})
            save_all(profiles)
            return JSONResponse({"ok": True, "items": items(), "active": getattr(cfg.llm, "profile", "")})

        if action == "duplicate":
            if name not in names:
                return JSONResponse({"ok": False, "message": "配置不存在"}, status_code=404)
            source = next(item for item in profiles if item["name"] == name)
            copy_name = str(body.get("new_name") or f"{name} 副本").strip()
            if copy_name in names:
                return JSONResponse({"ok": False, "message": "同名配置已存在"}, status_code=400)
            profiles.append({**source, "name": copy_name})
            save_all(profiles)
            return JSONResponse({"ok": True, "items": items()})

        if action == "delete":
            if any(item.get("template") and item["name"] == name for item in profiles):
                return JSONResponse({"ok": False, "message": "模板不能删"}, status_code=400)
            if name == getattr(cfg.llm, "profile", ""):
                return JSONResponse({"ok": False, "message": "正在用的配置不能删"}, status_code=400)
            if len(profiles) <= 1:
                return JSONResponse({"ok": False, "message": "至少留一套配置"}, status_code=400)
            profiles = [item for item in profiles if item["name"] != name]
            save_all(profiles)
            return JSONResponse({"ok": True, "items": items()})

        if action == "reorder":
            names_in_order = body.get("names")
            if not isinstance(names_in_order, list) or not names_in_order:
                return JSONResponse({"ok": False, "message": "缺少 names"}, status_code=400)
            by_name = {item["name"]: item for item in profiles}
            reordered = [by_name[name] for name in names_in_order if name in by_name]
            reordered += [item for item in profiles if item["name"] not in set(names_in_order)]
            save_all(reordered)
            return JSONResponse({"ok": True, "items": items()})

        if action == "save":
            # 把当前 [llm] 的值存回它自己那张卡
            current = getattr(cfg.llm, "profile", "")
            if not current:
                return JSONResponse({"ok": False, "message": "当前没有生效的配置"}, status_code=400)
            for index, item in enumerate(profiles):
                if item["name"] == current:
                    profiles[index] = {"name": current, **_snapshot(cfg)}
                    break
            else:
                profiles.append({"name": current, **_snapshot(cfg)})
            save_all(profiles)
            return JSONResponse({"ok": True, "items": items()})

        return JSONResponse({"ok": False, "message": f"不认识的动作 {action}"}, status_code=400)




