"""插件管理接口。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..config import Config
from ..pluginkit.registry import ExtensionRegistry
from ..pluginkit.manifest import read_manifest
from ..pluginkit.settings_store import defaults_from

log = logging.getLogger(__name__)


def mount_plugin_routers(app: FastAPI, registry: ExtensionRegistry) -> None:
    """把插件自带的面板挂上去。重复调用只会补挂新增的。"""
    from fastapi.responses import FileResponse

    from fastapi.responses import RedirectResponse

    mounted: set[str] = getattr(app.state, "plugin_pages", set())
    # 允许不带尾斜杠访问：页面里的相对路径要按 /plugins/<id>/ 解析
    for name in registry.pages:
        try:
            app.add_api_route(
                f"/plugins/{name}",
                lambda name=name: RedirectResponse(f"/plugins/{name}/"),
                methods=["GET"],
                include_in_schema=False,
            )
        except Exception:  # noqa: BLE001
            pass
    for name, page in registry.pages.items():
        if not page or name in mounted:
            continue
        try:
            app.add_api_route(
                f"/plugins/{name}/",
                lambda page=page: FileResponse(page, media_type="text/html"),
                methods=["GET"],
                include_in_schema=False,
            )
            mounted.add(name)
            log.info("插件页面已挂载: /plugins/%s/", name)
        except Exception:  # noqa: BLE001
            log.exception("挂载插件页面失败: %s", name)
    app.state.plugin_pages = mounted

    mounted_routers: set[str] = getattr(app.state, "plugin_routers", set())
    for name, router in registry.routers.items():
        if name in mounted_routers:
            continue
        try:
            app.include_router(router, prefix=f"/plugins/{name}", tags=[f"plugin:{name}"])
            mounted_routers.add(name)
            log.info("插件面板已挂载: /plugins/%s", name)
        except Exception:  # noqa: BLE001
            log.exception("挂载插件面板失败: %s", name)
    app.state.plugin_routers = mounted_routers


def mount_plugin_api(app: FastAPI, cfg: Config, registry: ExtensionRegistry, save_enabled) -> None:
    """插件列表 / 启停 / 设置的接口。save_enabled 负责把 enabled 写回主配置。"""

    @app.get("/api/plugins")
    async def list_plugins() -> JSONResponse:
        return JSONResponse({"plugins": registry.list_plugins(), "enabled": list(registry.enabled)})

    @app.post("/api/plugins/{name}/enabled")
    async def toggle(name: str, request: Request) -> JSONResponse:
        try:
            body: dict[str, Any] = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请求体不是 JSON") from exc
        enabled = bool(body.get("enabled"))
        manifest = registry.manifests.get(name) or read_manifest(registry.root_of(name), name)
        if manifest.error:
            return JSONResponse({"ok": False, "message": manifest.error}, status_code=400)

        await registry.set_enabled(name, enabled)
        save_enabled(list(registry.enabled))
        mount_plugin_routers(app, registry)
        log.info("插件 %s 已%s", name, "启用" if enabled else "停用")
        return JSONResponse(
            {
                "ok": True,
                "plugins": registry.list_plugins(),
                "restart": False,
                "message": f"{manifest.display} 已{'启用' if enabled else '停用'}",
            }
        )

    @app.get("/api/plugins/{name}/settings")
    async def read_settings(name: str) -> JSONResponse:
        """打开设置弹窗时现算一遍字段：插件可以在这里补动态选项。"""
        manifest = registry.manifests.get(name) or read_manifest(registry.root_of(name), name)
        if manifest.error:
            return JSONResponse({"ok": False, "message": manifest.error}, status_code=400)
        fields = await registry.settings_schema(name)
        values = registry.settings_of(name, defaults_from(fields))
        return JSONResponse({"ok": True, "fields": fields, "values": values})

    @app.post("/api/plugins/{name}/settings")
    async def save_settings(name: str, request: Request) -> JSONResponse:
        try:
            body: dict[str, Any] = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请求体不是 JSON") from exc
        values = body.get("values")
        if not isinstance(values, dict):
            return JSONResponse({"ok": False, "message": "缺少 values"}, status_code=400)

        manifest = registry.manifests.get(name) or read_manifest(registry.root_of(name), name)
        if manifest.error:
            return JSONResponse({"ok": False, "message": manifest.error}, status_code=400)

        clean: dict[str, Any] = {}
        fields = await registry.settings_schema(name)
        defaults = defaults_from(fields)
        for field in fields:
            key = field["key"]
            if key not in values:
                continue
            raw = values[key]
            kind = field.get("type", "text")
            try:
                if kind == "number":
                    clean[key] = float(raw) if isinstance(raw, float) else int(raw)
                elif kind == "bool":
                    clean[key] = bool(raw)
                else:
                    clean[key] = str(raw)
            except (TypeError, ValueError):
                return JSONResponse(
                    {"ok": False, "message": f"{field.get('label', key)} 不是合法数值"}, status_code=400
                )
            low, high = field.get("min"), field.get("max")
            if kind == "number" and isinstance(clean[key], (int, float)):
                if low is not None and clean[key] < low:
                    return JSONResponse({"ok": False, "message": f"{field.get('label', key)} 不能小于 {low}"}, status_code=400)
                if high is not None and clean[key] > high:
                    return JSONResponse({"ok": False, "message": f"{field.get('label', key)} 不能大于 {high}"}, status_code=400)

        merged = registry.update_settings(name, {**defaults, **clean})
        return JSONResponse({"ok": True, "settings": merged, "message": "设置已保存"})






