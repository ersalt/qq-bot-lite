"""内建插件：运行统计。

演示一个插件需要的全部东西：MANIFEST（含可在面板里编辑的设置）、
一个挂到 /plugins/stats 的小面板，以及可选的聊天钩子。
"""

from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from .base import Extension, HookContext
from .manifest import PluginContext

MANIFEST = {
    "title": "运行统计",
    "description": "运行时长、收发条数、首字延迟和数据库规模，面板里实时刷新。",
    "version": "0.1.0",
    "author": "内建",
    "panel": "/plugins/stats",
    "config": [
        {
            "key": "window_hours",
            "label": "统计窗口（小时）",
            "type": "number",
            "min": 1,
            "max": 720,
            "step": 1,
            "default": 24,
            "help": "面板上那句“最近 N 小时”就是它",
        },
        {
            "key": "show_latency",
            "label": "显示首字延迟",
            "type": "bool",
            "default": True,
            "help": "关掉就只显示收发条数",
        },
    ],
}

router = APIRouter()


def _service(ctx: PluginContext):
    return ctx.services.get("service")


def _store(ctx: PluginContext):
    return ctx.services.get("store")


@router.get("/data")
async def data() -> JSONResponse:
    ctx = _CONTEXT
    service = _service(ctx) if ctx else None
    store = _store(ctx) if ctx else None
    payload = {
        "uptime": round(getattr(service, "uptime_seconds", 0), 0),
        "received": getattr(service, "messages_seen", 0),
        "replied": getattr(service, "replies_sent", 0),
        "first_ms": round(getattr(service, "average_first_ms", 0), 0),
        "window_hours": (ctx.settings.get("window_hours", 24) if ctx else 24),
        "show_latency": bool(ctx.settings.get("show_latency", True)) if ctx else True,
        "db": store.stats() if store else {},
        "now": time.strftime("%H:%M:%S"),
    }
    return JSONResponse(payload)


@router.get("/", response_class=HTMLResponse)
async def panel() -> str:
    return _PANEL


_PANEL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>运行统计</title>
<style>
 body{font:14px/1.7 "Segoe UI",system-ui,sans-serif;background:#f8fafc;color:#1f2937;margin:0;padding:32px}
 main{max-width:640px;margin:0 auto}
 h1{font-size:18px;margin:0 0 4px}
 p.sub{color:#6b7280;margin:0 0 22px;font-size:12.5px}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
 .cell{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px 16px}
 .k{color:#6b7280;font-size:12px}
 .v{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
 @media (prefers-color-scheme: dark){
   body{background:#101418;color:#e5e8ed} .cell{background:#181d24;border-color:#252b34} .k{color:#8b929e}
 }
</style></head><body><main>
<h1>运行统计</h1>
<p class="sub" id="sub">读取中</p>
<div class="grid" id="grid"></div>
</main>
<script>
async function tick(){
  try{
    const r = await fetch('/plugins/stats/data', {cache:'no-store'});
    const d = await r.json();
    const cells = [
      ['运行时长', fmtDuration(d.uptime)],
      ['收到消息', d.received],
      ['发出回复', d.replied],
    ];
    if (d.show_latency) cells.push(['首字均值', Math.round(d.first_ms) + ' ms']);
    cells.push(['数据库消息', d.db.messages ?? '-']);
    cells.push(['会话数', d.db.sessions ?? '-']);
    document.getElementById('grid').innerHTML = cells.map(
      ([k,v]) => `<div class="cell"><div class="k">${k}</div><div class="v">${v}</div></div>`
    ).join('');
    document.getElementById('sub').textContent = `统计窗口 ${d.window_hours} 小时 · 更新于 ${d.now}`;
  }catch(e){ document.getElementById('sub').textContent = '面板不可用：' + e.message; }
}
function fmtDuration(s){
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s/3600), m = Math.floor(s%3600/60);
  return h ? `${h} 小时 ${m} 分` : `${m} 分 ${s%60} 秒`;
}
tick(); setInterval(tick, 3000);
</script></body></html>"""


_CONTEXT: PluginContext | None = None


class StatsExtension(Extension):
    name = "stats"

    async def on_message(self, ctx: HookContext) -> None:
        # 插件不参与判定，只是占位演示钩子存在。
        return None


def setup(ctx: PluginContext) -> StatsExtension:
    global _CONTEXT
    _CONTEXT = ctx
    return StatsExtension()
