"""设置面板的接口测试：读、写、校验、注释保留。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import load_config
from app.main import build_app

BASE_TOML = """\
# 测试用配置
[llm]
# 模型标识
model = "old-model"
# temperature = 0.8
max_tokens = 0

[context]
max_messages = 100

[reply]
quote = true
"""


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    path = tmp_path / "config.toml"
    path.write_text(BASE_TOML, encoding="utf-8")
    cfg = load_config(path)
    app = build_app(cfg, db_path=tmp_path / "web.db", config_path=path)
    return TestClient(app), path


def test_page_and_schema(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        page = client.get("/")
        assert page.status_code == 200
        assert "qq-bot-lite" in page.text

        payload = client.get("/api/config").json()
        ids = [section["id"] for section in payload["sections"]]
        assert "llm" in ids and "images" in ids and "reply" in ids
        assert payload["values"]["llm.model"] == "old-model"
        assert payload["values"]["context.max_messages"] == 100


def test_save_writes_toml_and_applies(tmp_path: Path) -> None:
    client, path = _client(tmp_path)
    with client:
        response = client.post(
            "/api/config",
            json={
                "updates": {
                    "llm.model": "new-model",
                    "context.max_messages": 60,
                    "reply.quote": False,
                    "llm.temperature": 0.7,
                }
            },
        )
        assert response.status_code == 200, response.text
        values = response.json()["values"]
        assert values["llm.model"] == "new-model"
        assert values["llm.temperature"] == 0.7

    text = path.read_text(encoding="utf-8")
    assert 'model = "new-model"' in text
    assert "max_messages = 60" in text
    assert "quote = false" in text
    # 原本是注释掉的 temperature，应该原地取消注释，而不是追加到文件末尾
    assert "temperature = 0.7" in text
    assert "# temperature = 0.8" not in text

    fresh = load_config(path)
    assert fresh.llm.model == "new-model"
    assert fresh.context.max_messages == 60
    assert fresh.reply.quote is False
    assert fresh.llm.temperature == 0.7


def test_invalid_value_is_rejected(tmp_path: Path) -> None:
    client, path = _client(tmp_path)
    with client:
        response = client.post("/api/config", json={"updates": {"context.max_messages": 1}})
        assert response.status_code == 400
        errors = response.json()["errors"]
        assert errors and errors[0]["field"] == "context.max_messages"
    # 被拒绝时不能动文件
    assert "max_messages = 60" not in path.read_text(encoding="utf-8")


def test_restart_fields_reported(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        response = client.post("/api/config", json={"updates": {"llm.base_url": "http://127.0.0.1:9999/v1"}})
        assert response.status_code == 200
        restart = response.json()["restart"]
        assert any("接口地址" in item for item in restart)


def test_persona_edit_roundtrip(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    persona_file = tmp_path / "persona.md"
    persona_file.write_text("旧人设", encoding="utf-8")
    with client:
        client.post("/api/config", json={"updates": {"persona.file": str(persona_file)}})
        before = client.get("/api/persona").json()
        assert before["text"] == "旧人设"

        response = client.post("/api/persona", json={"text": "新人设\n不超过两句话。"})
        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        assert persona_file.read_text(encoding="utf-8") == "新人设\n不超过两句话。"

        empty = client.post("/api/persona", json={"text": "   "})
        assert empty.status_code == 400


def test_llm_check_reports_failure_gracefully(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        # 指向一个没人监听的端口，应该返回 ok=false 而不是抛 500
        client.post("/api/config", json={"updates": {"llm.base_url": "http://127.0.0.1:9/v1"}})
        payload = client.get("/api/llm/check").json()
        assert payload["ok"] is False
        assert payload["message"]


def test_plugins_listed_with_manifest(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        payload = client.get("/api/plugins").json()
        names = [item["name"] for item in payload["plugins"]]
        assert "stats" in names, payload
        stats = next(item for item in payload["plugins"] if item["name"] == "stats")
        assert stats["title"] == "运行统计"
        assert stats["panel"] == "/plugins/stats"
        assert stats["error"] is None
        # 测试用的配置里没有启用任何插件
        assert stats["enabled"] is False


def test_plugin_settings_roundtrip(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        response = client.post(
            "/api/plugins/stats/settings",
            json={"values": {"window_hours": 48, "show_latency": False}},
        )
        assert response.status_code == 200, response.text
        saved = response.json()["settings"]
        assert saved["window_hours"] == 48
        assert saved["show_latency"] is False

        bad = client.post("/api/plugins/stats/settings", json={"values": {"window_hours": 99999}})
        assert bad.status_code == 400

    # 落在配置文件同目录的 plugins.toml 里
    text = (tmp_path / "plugins.toml").read_text(encoding="utf-8")
    assert "window_hours = 48" in text
    assert "show_latency = false" in text


def test_plugin_toggle_writes_config(tmp_path: Path) -> None:
    client, path = _client(tmp_path)
    with client:
        response = client.post("/api/plugins/stats/enabled", json={"enabled": True})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        stats = next(item for item in payload["plugins"] if item["name"] == "stats")
        assert stats["enabled"] is True
        assert 'enabled = ["stats"]' in path.read_text(encoding="utf-8")

        off = client.post("/api/plugins/stats/enabled", json={"enabled": False})
        assert off.status_code == 200
    assert "enabled = []" in path.read_text(encoding="utf-8")
