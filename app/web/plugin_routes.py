"""插件管理接口。"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..config import Config
from ..pluginkit.archive import MAX_ZIP, InstallError, install_zip
from ..pluginkit.manifest import read_manifest
from ..pluginkit.registry import ExtensionRegistry
from ..pluginkit.settings_store import defaults_from

log = logging.getLogger(__name__)

# README 只做展示，不执行、不渲染；超过这个大小截断，避免把几 MB 的文档塞进页面。
README_LIMIT = 256 * 1024
_README_NAMES = ("readme.md", "readme.markdown", "readme.txt")


def _find_readme(folder: Path) -> Path | None:
    """在插件根目录找 README，大小写不敏感；不递归。"""
    try:
        entries = list(folder.iterdir())
    except OSError:
        return None
    files = {item.name.lower(): item for item in entries if item.is_file()}
    for name in _README_NAMES:
        if name in files:
            return files[name]
    return None


def mount_plugin_routers(app: FastAPI, registry: ExtensionRegistry) -> None:
    """把插件自带的面板挂上去。重复调用只会补挂新增的。"""
    from fastapi.responses import FileResponse, RedirectResponse

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

    @app.post("/api/plugins/install")
    async def install_plugin(request: Request) -> JSONResponse:
        """手动安装插件 ZIP。只收单个 <id>/plugin.toml 结构，安装后默认不启用。"""
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_ZIP:
            return JSONResponse(
                {"ok": False, "message": f"ZIP 超过 {MAX_ZIP // 1048576}MB 上限"}, status_code=413
            )
        data = await request.body()
        try:
            name = install_zip(data, registry.plugin_dir)
        except InstallError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=exc.status)
        registry.scan()
        manifest = registry.manifests.get(name)
        if manifest is None:
            return JSONResponse({"ok": False, "message": "安装后没有找到插件清单"}, status_code=500)
        log.info("插件已安装: %s", name)
        return JSONResponse(
            {
                "ok": True,
                "plugins": registry.list_plugins(),
                "message": f"已安装「{manifest.display}」（未启用），点卡片开关启用",
            }
        )

    @app.get("/api/plugins/{name}/readme")
    async def plugin_readme(name: str) -> JSONResponse:
        """插件 README 原文。只读文本，前端用 textContent 展示。"""
        if not registry.manifests:
            registry.scan()
        if name not in registry.manifests:
            raise HTTPException(status_code=404, detail="插件不存在")
        folder = registry.root_of(name) / name
        path = _find_readme(folder)
        if path is None:
            return JSONResponse(
                {"ok": False, "message": "这个插件没有 README"}, status_code=404
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return JSONResponse({"ok": False, "message": f"读取失败：{exc}"}, status_code=500)
        text = raw[:README_LIMIT].decode("utf-8", errors="replace").lstrip("\ufeff")
        return JSONResponse(
            {
                "ok": True,
                "name": name,
                "filename": path.name,
                "text": text,
                "truncated": len(raw) > README_LIMIT,
            }
        )

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

    @app.post("/api/plugins/{name}/reload")
    async def reload_plugin(name: str) -> JSONResponse:
        """重载单个插件的代码与设置。router 的增删仍要重启进程。"""
        if not registry.manifests:
            registry.scan()
        if name not in registry.manifests:
            raise HTTPException(status_code=404, detail="插件不存在")
        try:
            await registry.reload(name)
        except Exception as exc:  # noqa: BLE001
            log.exception("插件重载失败: %s", name)
            return JSONResponse({"ok": False, "message": f"重载失败：{exc}"}, status_code=500)
        mount_plugin_routers(app, registry)
        manifest = registry.manifests.get(name)
        error = registry.errors.get(name) or (manifest.error if manifest else None)
        title = manifest.display if manifest else name
        if error:
            message = f"已重载「{title}」，但加载失败：{error}"
        else:
            message = f"已重载「{title}」；如果改过 router，需要重启进程"
        log.info("插件重载: %s%s", name, f" 失败: {error}" if error else "")
        return JSONResponse(
            {
                "ok": not error,
                "plugins": registry.list_plugins(),
                "restart": False,
                "message": message,
            }
        )

    @app.delete("/api/plugins/{name}")
    async def delete_plugin(name: str, request: Request) -> JSONResponse:
        """删除插件目录；可选连持久化数据一起删。

        先把目录改名成隐藏的 .deleting-*（原子消失，discover 会跳过），再清理。
        这样即使文件被占用，面板上也不会再出现半个插件。
        """
        if not registry.manifests:
            registry.scan()
        if name not in registry.manifests:
            raise HTTPException(status_code=404, detail="插件不存在")

        root = registry.root_of(name)
        folder = (root / name).resolve()
        try:
            folder.relative_to(root.resolve())
        except ValueError:
            return JSONResponse({"ok": False, "message": "插件目录不在 plugins/ 下"}, status_code=400)
        if not folder.is_dir():
            return JSONResponse({"ok": False, "message": "插件目录不存在"}, status_code=404)

        with_data = request.query_params.get("data", "").lower() in {"1", "true", "yes"}
        had_panel = bool(registry.pages.get(name)) or name in registry.routers
        await registry.unload(name)

        trash = root / f".deleting-{name}-{int(time.time())}"
        try:
            folder.rename(trash)
        except OSError as exc:
            # 改名失败：还在 enabled 里，重新加载把它恢复回去
            log.exception("删除插件失败（改名）: %s", name)
            await registry.reload(name)
            return JSONResponse({"ok": False, "message": f"删除失败：{exc}"}, status_code=500)

        if name in registry.enabled:
            registry.enabled.remove(name)
            save_enabled(list(registry.enabled))
        registry.scan()

        warning = ""
        data_removed = False
        if with_data:
            data_dir = registry.plugin_data_dir(name).resolve()
            try:
                data_dir.relative_to(registry.data_dir.resolve())
                if data_dir.exists():
                    shutil.rmtree(data_dir)
                data_removed = True
            except (OSError, ValueError) as exc:
                log.warning("删除插件数据失败 %s: %s", name, exc)
                warning = f"插件已删除，但数据目录没删干净：{exc}"

        shutil.rmtree(trash, ignore_errors=True)
        if trash.exists():
            log.warning("插件临时目录没删干净: %s", trash)
            warning = f"{warning} 临时文件残留：{trash}".strip()

        if with_data and data_removed:
            message = f"已删除「{name}」和它的持久化数据"
        else:
            message = f"已删除「{name}」，持久化数据保留在 data/plugin_data/{name}/"
        if had_panel:
            message += "；面板路由要重启进程才彻底消失"
        log.info("插件已删除: %s（数据=%s）", name, "删除" if with_data else "保留")
        return JSONResponse(
            {
                "ok": True,
                "plugins": registry.list_plugins(),
                "message": message,
                "data_removed": data_removed,
                "warning": warning,
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
                    return JSONResponse(
                        {"ok": False, "message": f"{field.get('label', key)} 不能小于 {low}"},
                        status_code=400,
                    )
                if high is not None and clean[key] > high:
                    return JSONResponse(
                        {"ok": False, "message": f"{field.get('label', key)} 不能大于 {high}"},
                        status_code=400,
                    )

        merged = registry.update_settings(name, {**defaults, **clean})
        return JSONResponse({"ok": True, "settings": merged, "message": "设置已保存"})






