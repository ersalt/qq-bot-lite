"""模型配置管理：多套配置分开存，切换时写回 [llm]。

- `config/models.toml` 存所有配置（一个 `[[profile]]` 一张卡）
- `config/config.toml` 的 `[llm]` 始终是**当前生效**的那套
- 切换 = 把卡里的值抄进 [llm] + 热应用 + 重建 http 客户端，不用重启
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..config import Config, LLMConfig
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


def _write(path: Path, profiles: list[dict[str, Any]]) -> None:
    lines = ["# 模型配置。每张 [[profile]] 是一套完整参数，切换时会抄进 config.toml 的 [llm]。"]
    for profile in profiles:
        lines.append("")
        lines.append("[[profile]]")
        lines.append(f'name = {_fmt(profile["name"])}')
        for key in FIELDS:
            if key in profile and profile[key] is not None:
                lines.append(f"{key} = {_fmt(profile[key])}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _snapshot(cfg: LLMConfig) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key in FIELDS:
        value = getattr(cfg, key, None)
        if value is not None:
            data[key] = value
    return data


def mount_model_api(app: FastAPI, cfg: Config, llm: LLMClient, config_path: Path) -> None:
    path = (config_path.parent) / "models.toml"

    if not path.exists():
        # 第一次跑：把当前 [llm] 存成一张卡，侧栏不至于空着。
        seed_name = "默认"
        _write(path, [{"name": seed_name, **_snapshot(cfg.llm)}])
        cfg.llm.profile = seed_name
        try:
            save_to_toml(config_path, {"llm.profile": seed_name})
        except OSError:
            log.warning("写回 llm.profile 失败", exc_info=True)

    def items() -> list[dict[str, Any]]:
        current = _snapshot(cfg.llm)
        result = []
        for profile in _read(path):
            merged = {**current, **profile}
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
        updates = {}
        for key in FIELDS:
            if key not in profile:
                continue
            value = profile[key]
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
        cfg.llm.profile = str(profile["name"])
        updates["llm.profile"] = str(profile["name"])
        save_to_toml(config_path, updates)

    @app.get("/api/models")
    async def list_models() -> JSONResponse:
        return JSONResponse(
            {"items": items(), "active": getattr(cfg.llm, "profile", ""), "values": _snapshot(cfg.llm)}
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
            if name not in names:
                return JSONResponse({"ok": False, "message": "配置不存在"}, status_code=404)
            profile = next(item for item in profiles if item["name"] == name)
            apply(profile)
            await llm.reload()
            log.info("切换模型配置: %s", name)
            return JSONResponse({"ok": True, "items": items(), "active": name, "values": _snapshot(cfg.llm)})

        if action == "create":
            if not name:
                return JSONResponse({"ok": False, "message": "名字不能为空"}, status_code=400)
            if name in names:
                return JSONResponse({"ok": False, "message": "同名配置已存在"}, status_code=400)
            profile = {"name": name, **_snapshot(cfg.llm)}
            profiles.append(profile)
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
            if name == getattr(cfg.llm, "profile", ""):
                return JSONResponse({"ok": False, "message": "正在用的配置不能删"}, status_code=400)
            if len(profiles) <= 1:
                return JSONResponse({"ok": False, "message": "至少留一套配置"}, status_code=400)
            profiles = [item for item in profiles if item["name"] != name]
            save_all(profiles)
            return JSONResponse({"ok": True, "items": items()})

        if action == "save":
            # 把当前 [llm] 的值存回它自己那张卡
            current = getattr(cfg.llm, "profile", "")
            if not current:
                return JSONResponse({"ok": False, "message": "当前没有生效的配置"}, status_code=400)
            for index, item in enumerate(profiles):
                if item["name"] == current:
                    profiles[index] = {"name": current, **_snapshot(cfg.llm)}
                    break
            else:
                profiles.append({"name": current, **_snapshot(cfg.llm)})
            save_all(profiles)
            return JSONResponse({"ok": True, "items": items()})

        return JSONResponse({"ok": False, "message": f"不认识的动作 {action}"}, status_code=400)

