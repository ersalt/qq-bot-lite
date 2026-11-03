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
    personas = tmp_path / "personas"
    personas.mkdir(parents=True, exist_ok=True)
    (personas / "default.md").write_text("# 默认人设\n\n你是测试机器人。", encoding="utf-8")
    cfg.persona.file = "personas/default.md"
    app = build_app(
        cfg,
        db_path=tmp_path / "web.db",
        config_path=path,
        plugin_data_dir=tmp_path / "plugin_data",
        persona_dir=personas,
    )
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
        response = client.post("/api/config", json={"updates": {"server.port": 8899}})
        assert response.status_code == 200
        restart = response.json()["restart"]
        assert any("端口" in item for item in restart)


def test_persona_manage_and_edit(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        # 测试配置指向默认人设文件，先把它指到一个临时目录下的文件
        listing = client.get("/api/personas").json()
        assert listing["items"], listing

        created = client.post("/api/personas/create", json={"name": "测试人设"})
        assert created.status_code == 200, created.text
        assert any(item["name"] == "测试人设" for item in created.json()["items"])

        activated = client.post("/api/personas/active", json={"name": "测试人设"})
        assert activated.status_code == 200, activated.text
        assert activated.json()["active"] == "测试人设"

        before = client.get("/api/persona").json()
        assert "人设名" not in before["text"]
        assert "测试人设" in before["text"]

        saved = client.post("/api/persona", json={"text": "新人设\n不超过两句话。"})
        assert saved.status_code == 200, saved.text
        assert client.get("/api/persona").json()["text"] == "新人设\n不超过两句话。"

        empty = client.post("/api/persona", json={"text": "   "})
        assert empty.status_code == 400

        # 正在用的人设不能删
        blocked = client.request("DELETE", "/api/personas/测试人设")
        assert blocked.status_code == 400

        switched = client.post("/api/personas/active", json={"name": listing["active"]})
        assert switched.status_code == 200
        removed = client.request("DELETE", "/api/personas/测试人设")
        assert removed.status_code == 200, removed.text


def test_model_profiles_activate_and_delete(tmp_path: Path) -> None:
    client, config_path = _client(tmp_path)
    with client:
        listing = client.get("/api/models").json()
        assert listing["items"], "首次启动应该自动存一张默认卡"

        created = client.post("/api/models", json={"action": "create", "name": "备用"})
        assert created.status_code == 200, created.text
        names = [item["name"] for item in created.json()["items"]]
        assert "备用" in names

        active = client.post("/api/models", json={"action": "activate", "name": "备用"})
        assert active.status_code == 200, active.text
        assert active.json()["active"] == "备用"
        assert 'profile = "备用"' in config_path.read_text(encoding="utf-8")

        # 正在用的配置不能删
        blocked = client.post("/api/models", json={"action": "delete", "name": "备用"})
        assert blocked.status_code == 400


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
    text = (tmp_path / "plugin_data" / "stats" / "settings.toml").read_text(encoding="utf-8")
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



def test_model_template_is_hidden_and_copied(tmp_path: Path) -> None:
    client, config_path = _client(tmp_path)
    with client:
        listing = client.get("/api/models").json()
        assert all(item["name"] != "default" for item in listing["items"]), "模板不该出现在列表里"

        created = client.post("/api/models", json={"action": "create", "name": "抄模板"})
        assert created.status_code == 200, created.text

    text = (tmp_path / "models.toml").read_text(encoding="utf-8")
    assert "template = true" in text
    blocks = text.split("[[profile]]")
    template_block = next(block for block in blocks if "template = true" in block)
    copy_block = next(block for block in blocks if 'name = "抄模板"' in block)
    for line in ("model =", "base_url =", "max_concurrency ="):
        assert line in template_block and line in copy_block


def test_persona_template_is_hidden_and_copied(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        created = client.post("/api/personas/create", json={"name": "新人"})
        assert created.status_code == 200, created.text
        names = [item["name"] for item in created.json()["items"]]
        assert "_template" not in names

    files = {path.name for path in (tmp_path / "personas").glob("*.md")}
    assert "_template.md" in files
    text = (tmp_path / "personas" / "新人.md").read_text(encoding="utf-8")
    template = (tmp_path / "personas" / "_template.md").read_text(encoding="utf-8")
    assert text == template.replace("人设名", "新人")
