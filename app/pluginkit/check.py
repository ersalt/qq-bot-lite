"""插件规范自检：结构、清单、导入、日志、文档。

    python -m app.pluginkit.check plugins/whale_lexicon
    python -m app.pluginkit.check            # 检查 plugins/ 下所有插件

只读文件、不导入插件代码，所以坏插件也能被检查。规则见 docs/plugin.md。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import META_FILE, PLUGIN_DIR
from .manifest import SUPPORTED_API, read_manifest

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
ABS_PATH = re.compile(r"(?:[A-Za-z]:\\\\|[A-Za-z]:\\[^\"']*\\|/home/|/Users/)")
MANUAL_PREFIX = re.compile(r"^\[[^\]]{1,20}\]\s")
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


@dataclass
class Report:
    name: str
    path: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_text(self) -> str:
        head = f"{self.name}  {'✓' if self.ok else '✗'}"
        lines = [head]
        lines += [f"  × {item}" for item in self.errors]
        lines += [f"  ! {item}" for item in self.warnings]
        return "\n".join(lines)


def python_files(plugin_dir: Path) -> list[Path]:
    return [
        path for path in plugin_dir.rglob("*.py")
        if "__pycache__" not in path.parts and ".venv" not in path.parts
    ]


def imported_modules(source: str) -> set[str]:
    """静态取出一个文件里 import 的所有模块名。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names if base)
    return names


def has_setup(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "setup"
        for node in tree.body
    )


def manual_log_prefix(source: str) -> bool:
    """日志里手写了 `[xxx] ` 前缀——ctx.log 会自动加，别写两遍。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in LOG_METHODS:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if MANUAL_PREFIX.match(first.value):
                return True
    return False


def check_plugin(plugin_dir: Path) -> Report:
    plugin_dir = plugin_dir.resolve()
    report = Report(name=plugin_dir.name, path=plugin_dir)
    root = plugin_dir.parent

    if not plugin_dir.is_dir():
        report.errors.append(f"目录不存在：{plugin_dir}")
        return report
    if not (plugin_dir / META_FILE).exists():
        report.errors.append(f"缺少 {META_FILE}（插件名、标题、版本都在这里）")

    manifest = read_manifest(root, plugin_dir.name)
    if manifest.error:
        report.errors.append(manifest.error)
    else:
        if not manifest.title or manifest.title == manifest.name:
            report.warnings.append("plugin.toml 没写 title，面板上会显示插件 id")
        if not SEMVER.match(manifest.version):
            report.warnings.append(f"version 建议用 x.y.z，现在是 {manifest.version!r}")
        if manifest.api > SUPPORTED_API:
            report.errors.append(f"api {manifest.api} 比主程序支持的 {SUPPORTED_API} 新")
        entry = plugin_dir / manifest.entry
        if not entry.exists():
            report.errors.append(f"缺少入口文件 {manifest.entry}")
        elif entry.exists() and not has_setup(entry.read_text(encoding="utf-8", errors="replace")):
            report.errors.append("入口文件没有 setup(ctx)")
        if manifest.panel and not (plugin_dir / "pages" / "index.html").exists():
            report.errors.append("声明了 panel 但缺少 pages/index.html")

    sources = {}
    for path in python_files(plugin_dir):
        sources[path] = path.read_text(encoding="utf-8", errors="replace")

    for path, source in sources.items():
        rel = path.relative_to(plugin_dir)
        for module in imported_modules(source):
            top = module.split(".")[0]
            if top != "app":
                continue
            if module == "app.sdk" or module.startswith("app.sdk."):
                continue
            report.errors.append(f"{rel} 引了 {module}，插件只能用 app.sdk")
        if ABS_PATH.search(source):
            report.warnings.append(f"{rel} 里写死了绝对路径")
        if plugin_dir.name not in ("_template",) and manual_log_prefix(source):
            report.warnings.append(f"{rel} 日志里手写了 [前缀]，ctx.log 会自动加")

    joined = "\n".join(sources.values())
    if "ctx.log" not in joined:
        report.warnings.append("没看到用 ctx.log 打日志，用户看不到这个插件的运行情况")

    if not (plugin_dir / "README.md").exists():
        report.warnings.append("没有 README.md，别人不知道怎么用")
    if not (plugin_dir / "CHANGELOG.md").exists():
        report.warnings.append("没有 CHANGELOG.md")
    if len(sources) > 1 and not (plugin_dir / "tests").exists():
        report.warnings.append("多文件插件建议带 tests/，用主程序同一个 venv 跑")
    page = plugin_dir / "pages" / "index.html"
    if page.exists() and page.stat().st_size > 200_000:
        report.warnings.append("pages/index.html 超过 200KB，考虑拆成几个文件")

    return report


def iter_plugins(root: Path):
    if root.is_dir() and (root / META_FILE).exists():
        yield root
        return
    if not root.is_dir():
        return
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / META_FILE).exists():
            yield child


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.pluginkit.check", description="插件规范自检")
    parser.add_argument("paths", nargs="*", help="插件目录，默认 plugins/")
    parser.add_argument("--quiet", action="store_true", help="只打有问题的")
    args = parser.parse_args(argv)

    targets: list[Path] = []
    for raw in (args.paths or [str(PLUGIN_DIR)]):
        targets.extend(iter_plugins(Path(raw)))
    if not targets:
        print("没找到插件（找不到 plugin.toml）")
        return 1

    failed = 0
    for plugin_dir in targets:
        report = check_plugin(plugin_dir)
        if args.quiet and report.ok and not report.warnings:
            continue
        print(report.as_text())
        if not report.ok:
            failed += 1
    print(f"\n{len(targets)} 个插件，{failed} 个有错误")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
