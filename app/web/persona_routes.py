"""人设管理：列出 personas/ 下的所有 md，切换、新建、删除。"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..config import ROOT, Config
from ..persona import Persona
from .settings import save_to_toml

log = logging.getLogger(__name__)

TEMPLATE = """# 人设名

你是……

## 回复风格

- 每次回复不超过两句话，总字数严格控制在50字以内。
"""


def _safe_name(raw: str) -> str:
    """只允许字母数字、中文、横线和下划线，防止跳出目录。"""
    name = str(raw or "").strip()
    name = re.sub(r"[\s/\\:*?\"<>|]+", "-", name).strip("-")
    if not name or name.startswith("."):
        raise ValueError("人设名不能为空或以点开头")
    return name[:60]


def mount_persona_api(
    app: FastAPI,
    cfg: Config,
    persona: Persona,
    config_path: Path,
    root: Path | None = None,
) -> None:
    root = (root or (config_path.parent.parent / "personas")).resolve()

    def active_name() -> str:
        path = Path(cfg.persona.file)
        return path.stem

    def template_text() -> str:
        """隐藏模板：新人设照它抄。文件丢了就用内置的最小模板补一份。

        注意里面必须保留 `人设名` 占位符，新建时才替换成真正的名字。
        """
        path = root / "_template.md"
        if not path.exists():
            try:
                root.mkdir(parents=True, exist_ok=True)
                path.write_text(TEMPLATE, encoding="utf-8")
            except OSError:
                return TEMPLATE
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return TEMPLATE

    def items() -> list[dict[str, Any]]:
        if not root.exists():
            return []
        result = []
        for path in sorted(root.glob("*.md")):
            if path.stem.startswith("_"):
                continue  # 下划线开头的是模板，不进列表
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            result.append(
                {
                    "name": path.stem,
                    "chars": len(text),
                    "updated": int(path.stat().st_mtime),
                    "active": path.stem == active_name(),
                }
            )
        order = list(getattr(cfg.persona, "order", []) or [])
        rank = {name: index for index, name in enumerate(order)}
        # 顺序只看 order 表；选中/新建都不该让卡片跳位置。
        result.sort(key=lambda item: (rank.get(item["name"], len(rank) + 1), item["name"]))
        return result

    @app.get("/api/personas")
    async def list_personas() -> JSONResponse:
        return JSONResponse({"items": items(), "active": active_name(), "dir": str(root)})

    @app.post("/api/personas/create")
    async def create_persona(request: Request) -> JSONResponse:
        try:
            body: dict[str, Any] = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请求体不是 JSON") from exc
        try:
            name = _safe_name(body.get("name"))
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

        path = root / f"{name}.md"
        if path.exists():
            return JSONResponse({"ok": False, "message": f"「{name}」已经存在"}, status_code=400)
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(template_text().replace("人设名", name), encoding="utf-8")
        order = [item for item in (getattr(cfg.persona, "order", []) or []) if item != name]
        order.append(name)
        cfg.persona.order = order
        save_to_toml(config_path, {"persona.order": order})
        log.info("新建人设: %s", path)
        return JSONResponse({"ok": True, "items": items(), "name": name})

    @app.post("/api/personas/active")
    async def set_active(request: Request) -> JSONResponse:
        try:
            body: dict[str, Any] = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请求体不是 JSON") from exc
        try:
            name = _safe_name(body.get("name"))
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

        path = root / f"{name}.md"
        if not path.exists():
            return JSONResponse({"ok": False, "message": f"「{name}」不存在"}, status_code=404)

        try:
            relative = str(path.relative_to(ROOT)).replace("\\", "/" )
        except ValueError:
            relative = str(path)  # 人设目录不在项目里时存绝对路径
        save_to_toml(config_path, {"persona.file": relative})
        cfg.persona.file = relative
        persona.reload()
        log.info("切换人设: %s", relative)
        return JSONResponse({"ok": True, "items": items(), "active": name, "text": persona.text})

    @app.delete("/api/personas/{name}")
    async def delete_persona(name: str) -> JSONResponse:
        try:
            safe = _safe_name(name)
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        if safe == active_name():
            return JSONResponse({"ok": False, "message": "正在用的人设不能删"}, status_code=400)
        if len(items()) <= 1:
            return JSONResponse({"ok": False, "message": "至少留一个人设"}, status_code=400)

        path = root / f"{safe}.md"
        if not path.exists():
            return JSONResponse({"ok": False, "message": "文件不存在"}, status_code=404)
        try:
            path.unlink()
        except OSError as exc:
            return JSONResponse({"ok": False, "message": f"删不掉：{exc}"}, status_code=500)
        log.info("删除人设: %s", path)
        return JSONResponse({"ok": True, "items": items()})





    @app.post("/api/personas/order")
    async def set_order(request: Request) -> JSONResponse:
        try:
            body: dict[str, Any] = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请求体不是 JSON") from exc
        names = body.get("names")
        if not isinstance(names, list) or not names:
            return JSONResponse({"ok": False, "message": "缺少 names"}, status_code=400)
        order = [str(item) for item in names if str(item).strip()]
        cfg.persona.order = order
        save_to_toml(config_path, {"persona.order": order})
        return JSONResponse({"ok": True, "items": items(), "order": order})

