"""插件规范：自检工具和日志前缀。"""

from __future__ import annotations

import logging
from pathlib import Path

from app.pluginkit import PLUGIN_DIR
from app.pluginkit.check import check_plugin
from app.pluginkit.plugin_log import make_plugin_logger


def _broken_plugin(tmp_path: Path) -> Path:
    root = tmp_path / "plugins" / "broken"
    root.mkdir(parents=True)
    (root / "plugin.toml").write_text(
        'title = "坏插件"\npanel = "/plugins/broken"\n', encoding="utf-8"
    )
    (root / "plugin.py").write_text(
        "from app.pluginkit.registry import Extension\n"
        "class Broken(Extension):\n"
        "    name = 'broken'\n",
        encoding="utf-8",
    )
    return root


def test_check_catches_common_mistakes(tmp_path: Path) -> None:
    root = _broken_plugin(tmp_path)
    report = check_plugin(root)
    text = report.as_text()

    assert not report.ok
    assert "setup" in text, "没查出缺少 setup(ctx)"
    assert "pages/index.html" in text, "没查出声明了 panel 却没页面"
    assert "app.pluginkit" in text, "没查出越权 import"


def test_template_plugin_passes(tmp_path: Path) -> None:
    report = check_plugin(PLUGIN_DIR / "_template")
    assert report.ok, report.as_text()


def test_demo_fixture_passes(tmp_path: Path) -> None:
    report = check_plugin(Path(__file__).parent / "fixtures" / "plugins" / "demo")
    assert report.ok, report.as_text()


def test_plugin_logger_prefixes_title() -> None:
    seen: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    base = logging.getLogger("plugin.demo_log")
    handler = Capture()
    base.addHandler(handler)
    base.setLevel(logging.INFO)
    try:
        make_plugin_logger("demo_log", "演示插件").info("干活了")
    finally:
        base.removeHandler(handler)

    assert seen and seen[-1] == "[演示插件] 干活了"
