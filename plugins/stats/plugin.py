"""内建插件：运行统计。

演示一个插件该有的东西：元数据在同目录的 plugin.toml 里，这里只写代码；
`ctx.data_dir` 是 `data/plugin_data/stats/`，用来放它自己的持久化数据。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from app.pluginkit.manifest import PluginContext
from app.pluginkit.registry import Extension, HookContext

router = APIRouter()

_CONTEXT: PluginContext | None = None
_SEEN = 0
_FLUSH_EVERY = 10


def _counter_path() -> Path | None:
    if _CONTEXT is None or _CONTEXT.data_dir is None:
        return None
    return _CONTEXT.data_dir / "counter.json"


def _read_total() -> int:
    path = _counter_path()
    if path is None or not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("total", 0))
    except (OSError, ValueError, TypeError):
        return 0


def _write_total(total: int) -> None:
    path = _counter_path()
    if path is None:
        return
    try:
        path.write_text(json.dumps({"total": total}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


@router.get("/data")
async def data() -> JSONResponse:
    ctx = _CONTEXT
    service = ctx.services.get("service") if ctx else None
    store = ctx.services.get("store") if ctx else None
    settings = ctx.settings if ctx else {}
    return JSONResponse(
        {
            "uptime": round(getattr(service, "uptime_seconds", 0), 0),
            "received": getattr(service, "messages_seen", 0),
            "replied": getattr(service, "replies_sent", 0),
            "first_ms": round(getattr(service, "average_first_ms", 0), 0),
            "total_seen": _read_total(),
            "window_hours": settings.get("window_hours", 24),
            "show_latency": bool(settings.get("show_latency", True)),
            "db": store.stats() if store else {},
            "data_dir": str(ctx.data_dir) if ctx and ctx.data_dir else "",
            "now": time.strftime("%H:%M:%S"),
        }
    )


@router.get("/", response_class=HTMLResponse)
async def panel() -> str:
    return _PANEL


class StatsPlugin(Extension):
    name = "stats"

    async def on_message(self, ctx: HookContext) -> None:
        global _SEEN
        _SEEN += 1
        if _SEEN % _FLUSH_EVERY == 0:
            _write_total(_read_total() + _FLUSH_EVERY)


def setup(ctx: PluginContext) -> StatsPlugin:
    global _CONTEXT
    _CONTEXT = ctx
    return StatsPlugin()


_PANEL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>运行统计</title>
<style>
 body{font:14px/1.7 "Segoe UI",system-ui,sans-serif;background:#f8fafc;color:#1f2937;margin:0;padding:32px}
 main{max-width:680px;margin:0 auto}
 h1{font-size:18px;margin:0 0 4px}
 p.sub{color:#6b7280;margin:0 0 22px;font-size:12.5px}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
 .cell{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px 16px}
 .k{color:#6b7280;font-size:12px}
 .v{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
 p.foot{color:#9ca3af;font-size:11.5px;margin-top:18px;word-break:break-all}
 @media (prefers-color-scheme: dark){
   body{background:#101418;color:#e5e8ed} .cell{background:#181d24;border-color:#252b34}
   .k{color:#8b929e} p.foot{color:#6b7280}
 }
</style></head><body><main>
<h1>运行统计</h1>
<p class="sub" id="sub">读取中</p>
<div class="grid" id="grid"></div>
<p class="foot" id="foot"></p>
</main>
<script>
function fmtDuration(s){
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s/3600), m = Math.floor(s%3600/60);
  return h ? `${h} 小时 ${m} 分` : `${m} 分 ${s%60} 秒`;
}
async function tick(){
  try{
    const r = await fetch('/plugins/stats/data', {cache:'no-store'});
    const d = await r.json();
    const cells = [
      ['运行时长', fmtDuration(d.uptime)],
      ['收到消息', d.received],
      ['发出回复', d.replied],
      ['累计看过', d.total_seen],
    ];
    if (d.show_latency) cells.push(['首字均值', Math.round(d.first_ms) + ' ms']);
    cells.push(['数据库消息', d.db.messages ?? '-']);
    cells.push(['会话数', d.db.sessions ?? '-']);
    document.getElementById('grid').innerHTML = cells.map(
      ([k,v]) => `<div class="cell"><div class="k">${k}</div><div class="v">${v}</div></div>`
    ).join('');
    document.getElementById('sub').textContent = `统计窗口 ${d.window_hours} 小时 · 更新于 ${d.now}`;
    document.getElementById('foot').textContent = '插件数据目录：' + d.data_dir;
  }catch(e){ document.getElementById('sub').textContent = '面板不可用：' + e.message; }
}
tick(); setInterval(tick, 3000);
</script></body></html>"""
