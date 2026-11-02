"""版本号单一来源校验：VERSION、代码、CHANGELOG 必须一致。"""

from __future__ import annotations

import re
from pathlib import Path

from app import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_version_file_and_code_agree() -> None:
    text = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert text == __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", text), f"VERSION 不是语义化版本: {text!r}"


def test_changelog_has_entry_for_current_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## v{__version__}" in changelog, "CHANGELOG 里没有当前版本的条目"
