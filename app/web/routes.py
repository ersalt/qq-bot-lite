"""设置面板的路由与静态资源。"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config, load_config
from ..persona import Persona
from .schema import SECTIONS
from .settings import (
    apply_to_config,
    collect_values,
    normalize,
    restart_fields,
    save_to_toml,
)

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
PAGE = HERE / "page.html"


def mount_web(
    app: FastAPI,
    cfg: Config,
    persona: Persona,
    config_path: Path,
    on_llm_change=None,
) -> None:
    """挂上首页、配置读写接口和静态资源。"""

    class _NoCacheStatic(StaticFiles):
        """静态资源永远回源校验：改完 CSS/JS 刷新即可，不用手动强刷。"""

        async def get_response(self, path, scope):  # type: ignore[override]
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

    app.mount("/static", _NoCacheStatic(directory=str(STATIC)), name="static")

    _ASSET_RE = re.compile(r'/static/([A-Za-z0-9_.-]+)')

    def _stamp_assets(html: str) -> str:
        """给资源加 ?v=<修改时间>，浏览器换新文件时一定重新下载。"""

        def repl(match: re.Match[str]) -> str:
            name = match.group(1)
            path = STATIC / name
            try:
                version = int(path.stat().st_mtime)
            except OSError:
                version = 0
            return f"/static/{name}?v={version}"

        return _ASSET_RE.sub(repl, html)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        from .. import __version__

        html = PAGE.read_text(encoding="utf-8").replace("__VERSION__", __version__)
        response = HTMLResponse(_stamp_assets(html))
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    @app.get("/api/config")
    async def read_config() -> JSONResponse:
        return JSONResponse(
            {
                "sections": SECTIONS,
                "values": collect_values(cfg),
                "path": str(config_path),
            }
        )

    @app.post("/api/config")
    async def write_config(request: Request) -> JSONResponse:
        try:
            body: dict[str, Any] = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请求体不是 JSON") from exc

        updates = body.get("updates")
        if not isinstance(updates, dict) or not updates:
            raise HTTPException(status_code=400, detail="没有要保存的项")

        clean, errors = normalize(updates)
        if errors:
            return JSONResponse({"ok": False, "errors": errors}, status_code=400)

        try:
            save_to_toml(config_path, clean)
        except OSError as exc:
            log.error("写入配置失败: %s", exc)
            return JSONResponse(
                {"ok": False, "errors": [{"field": "", "message": f"写文件失败：{exc}"}]},
                status_code=500,
            )

        apply_to_config(cfg, clean)
        if on_llm_change is not None and any(key.startswith("llm.") for key in clean):
            try:
                await on_llm_change()
            except Exception:  # noqa: BLE001 - 重建连接池失败不该拦住保存
                log.exception("重建模型连接失败")
        if "persona.file" in clean:
            try:
                persona.reload()
            except Exception:  # noqa: BLE001 - 人设改坏不能连累保存
                log.exception("重载人设失败")

        # 写完再读一遍，把 toml 里真实的落盘结果回给前端（类型、空值都以文件为准）。
        fresh = load_config(config_path)
        for section in SECTIONS:
            name = section["id"]
            for field in section["fields"]:
                attr = field["key"].split(".", 1)[1]
                setattr(getattr(cfg, name), attr, getattr(getattr(fresh, name), attr))

        log.info("配置已保存：%s", ", ".join(sorted(clean)))
        return JSONResponse(
            {
                "ok": True,
                "values": collect_values(cfg),
                "restart": restart_fields(clean),
                "errors": [],
            }
        )

    @app.get("/api/persona")
    async def read_persona() -> JSONResponse:
        path = cfg.persona_path
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return JSONResponse({"path": str(path), "text": text, "chars": len(text)})

    @app.post("/api/persona")
    async def write_persona(request: Request) -> JSONResponse:
        try:
            body: dict[str, Any] = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请求体不是 JSON") from exc
        text = str(body.get("text") or "")
        if not text.strip():
            return JSONResponse(
                {"ok": False, "message": "人设不能是空的"}, status_code=400
            )
        path = cfg.persona_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        try:
            persona.reload()
        except Exception as exc:  # noqa: BLE001
            log.exception("重载人设失败")
            return JSONResponse({"ok": False, "message": f"写入成功但重载失败：{exc}"}, status_code=500)
        log.info("人设已更新：%s（%d 字）", path, len(text))
        return JSONResponse({"ok": True, "chars": len(text), "path": str(path)})

    @app.get("/api/llm/check")
    async def check_llm() -> JSONResponse:
        """点一下就知道模型那头通不通、加载的是哪个模型。"""
        import time

        import httpx

        base = cfg.llm.base_url.rstrip("/")
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0), trust_env=False) as client:
                response = await client.get(
                    f"{base}/models",
                    headers={"Authorization": f"Bearer {cfg.llm.api_key}"} if cfg.llm.api_key else {},
                )
            elapsed = (time.perf_counter() - started) * 1000
            if response.status_code != 200:
                return JSONResponse(
                    {
                        "ok": False,
                        "message": f"HTTP {response.status_code}",
                        "latency_ms": round(elapsed),
                    }
                )
            models = [str(item.get("id")) for item in (response.json().get("data") or [])]
            loaded = cfg.llm.model in models
            return JSONResponse(
                {
                    "ok": True,
                    "latency_ms": round(elapsed),
                    "models": models[:20],
                    "loaded": loaded,
                    "message": "模型已加载" if loaded else "接口通，但当前模型不在已加载列表里",
                }
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.perf_counter() - started) * 1000
            return JSONResponse(
                {"ok": False, "message": f"{type(exc).__name__}: {exc}", "latency_ms": round(elapsed)}
            )


