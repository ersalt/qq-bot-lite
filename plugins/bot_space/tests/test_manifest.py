"""清单与结构自检：不导入插件代码，坏文件也能被检查。"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
SKIP_DIRS = {"__pycache__", ".git", ".venv", ".pytest_cache", ".ruff_cache"}
ABSOLUTE_PATH = re.compile(
    "|".join([r"[A-Za-z]:\\\\", r"/" + "home/", r"/" + "Users/"])
)


def manifest() -> dict:
    return tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))


def python_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*.py")
        if not (set(path.relative_to(ROOT).parts) & SKIP_DIRS)
    ]


def imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names if base)
    return names


def test_manifest_fields() -> None:
    data = manifest()
    assert data["title"] == "BOT空间"
    assert data["description"]
    assert SEMVER.match(str(data["version"])), f"version 不是 x.y.z：{data.get('version')}"
    assert data["api"] == 2
    assert data["panel"] == "/plugins/bot_space"
    keys = [item["key"] for item in data.get("config", [])]
    assert len(keys) == len(set(keys)), "config key 重复"
    assert "allow_write" in keys
    assert "max_file_kb" in keys


def test_config_types_and_limits() -> None:
    for item in manifest().get("config", []):
        assert item["type"] in {"bool", "number", "text", "options"}
        if item["type"] == "number":
            assert item["min"] <= item["default"] <= item["max"]


def test_entry_exposes_setup() -> None:
    entry = ROOT / str(manifest().get("entry", "plugin.py"))
    assert entry.exists(), f"缺少入口文件 {entry.name}"
    tree = ast.parse(entry.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "setup"
        for node in tree.body
    ), "入口没有 setup(ctx)"


def test_panel_page_exists() -> None:
    page = ROOT / "pages" / "index.html"
    assert page.exists(), "声明了 panel 却没有 pages/index.html"
    text = page.read_text(encoding="utf-8")
    assert text.strip()
    assert "http://" not in text and "https://" not in text, "面板不应加载外部资源"


def test_only_sdk_imports() -> None:
    for path in python_files():
        for module in imported_modules(path.read_text(encoding="utf-8")):
            if module.split(".")[0] != "app":
                continue
            assert module == "app.sdk" or module.startswith("app.sdk."), (
                f"{path.relative_to(ROOT)} 引了 {module}，插件只能 import app.sdk"
            )


def test_no_hardcoded_absolute_paths() -> None:
    for path in python_files():
        rel = path.relative_to(ROOT)
        if rel.parts[0] == "tests":
            continue
        assert not ABSOLUTE_PATH.search(path.read_text(encoding="utf-8")), (
            f"{rel} 里写死了绝对路径"
        )


def test_no_user_commands() -> None:
    source = (ROOT / "plugin.py").read_text(encoding="utf-8")
    assert "COMMANDS" not in source, "bot_space 设计上不提供用户命令"


def test_version_matches_plugin_py() -> None:
    source = (ROOT / "plugin.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', source, re.MULTILINE)
    assert match, "plugin.py 里找不到 VERSION"
    assert match.group(1) == manifest()["version"]


def test_docs_present() -> None:
    assert (ROOT / "README.md").exists()
    assert (ROOT / "CHANGELOG.md").exists()
    assert (ROOT / "LICENSE").exists()
