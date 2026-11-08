"""设置面板的接口测试：读、写、校验、注释保留。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import load_config
from app.main import build_app

FIXTURE_PLUGINS = Path(__file__).parent / "fixtures" / "plugins"


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


def _client(tmp_path: Path, plugin_dir: Path | None = None) -> tuple[TestClient, Path]:
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
        plugin_dir=plugin_dir,
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


def test_max_bytes_shown_in_kb_and_saved_as_bytes(tmp_path: Path) -> None:
    client, path = _client(tmp_path)
    with client:
        payload = client.get("/api/config").json()
        field = next(
            item
            for section in payload["sections"]
            if section["id"] == "images"
            for item in section["fields"]
            if item["key"] == "images.max_bytes"
        )
        assert field["scale"] == 1024
        assert field["label"].endswith("（KB）")
        assert field["hint"] == "1MB = 1024KB"
        # 默认 20,000,000 字节在面板上显示成 19531.25 KB
        assert payload["values"]["images.max_bytes"] == 19531.25

        response = client.post(
            "/api/config",
            json={"updates": {"images.max_bytes": 8192}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["values"]["images.max_bytes"] == 8192

    text = path.read_text(encoding="utf-8")
    assert "max_bytes = 8388608" in text


def test_example_config_enables_builtin_plugins() -> None:
    example = Path(__file__).resolve().parents[1] / "config" / "config.example.toml"
    cfg = load_config(example)
    assert cfg.extensions.enabled == ["bot_space", "whale_lexicon"]


def test_docs_api_and_no_standalone_page(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        page = client.get("/")
        assert page.status_code == 200
        assert 'id="theme"' in page.text
        assert 'id="full"' in page.text

        # 文档只通过主面板页签提供，旧 /manual 页面已移除
        assert client.get("/manual").status_code == 404

        listing = client.get("/api/docs").json()
        assert listing["ok"] is True
        assert any(item["path"] == "settings.md" for item in listing["items"])
        assert any(item["label"] == "SDK 与 API" for item in listing["items"])
        assert all("设计交接" not in item["label"] for item in listing["items"])

        development = client.get("/api/docs/development.md").json()
        assert development["ok"] is True
        assert "ToolRunner" in development["html"]
        assert 'href="#documents/guide.md"' in development["html"]

        settings = client.get("/api/docs/settings.md").json()
        assert settings["ok"] is True
        assert "接过的消息条数" in settings["html"]

        api_doc = client.get("/api/docs/api.md").json()
        assert api_doc["ok"] is True
        assert "PluginContext" in api_doc["html"]

        plugin = client.get("/api/docs/plugins/whale_lexicon/README.md").json()
        assert plugin["ok"] is True
        assert "鲸语汇" in plugin["html"]

        assert client.get("/api/docs/nope.md").status_code == 404


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


def test_switching_model_keeps_global_concurrency(tmp_path: Path) -> None:
    client, config_path = _client(tmp_path)
    with client:
        saved = client.post("/api/config", json={"updates": {"llm.max_concurrency": 1}})
        assert saved.status_code == 200, saved.text

        created = client.post("/api/models", json={"action": "create", "name": "另一个模型"})
        assert created.status_code == 200, created.text
        activated = client.post("/api/models", json={"action": "activate", "name": "另一个模型"})
        assert activated.status_code == 200, activated.text

    text = config_path.read_text(encoding="utf-8")
    assert "max_concurrency = 1" in text


def test_llm_check_reports_failure_gracefully(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        # 指向一个没人监听的端口，应该返回 ok=false 而不是抛 500
        client.post("/api/config", json={"updates": {"llm.base_url": "http://127.0.0.1:9/v1"}})
        payload = client.get("/api/llm/check").json()
        assert payload["ok"] is False
        assert payload["message"]


def test_plugins_listed_with_manifest(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, plugin_dir=FIXTURE_PLUGINS)
    with client:
        payload = client.get("/api/plugins").json()
        names = [item["name"] for item in payload["plugins"]]
        assert "demo" in names, payload
        demo = next(item for item in payload["plugins"] if item["name"] == "demo")
        assert demo["title"] == "演示插件"
        assert demo["panel"] == "/plugins/demo"
        assert demo["repository"] == "https://github.com/ersalt/qq-bot-lite"
        assert demo["error"] is None
        # 测试用的配置里没有启用任何插件
        assert demo["enabled"] is False


def test_plugin_hot_reload_and_stop(tmp_path: Path) -> None:
    root = tmp_path / "plugins" / "hot"
    root.mkdir(parents=True)
    (root / "plugin.toml").write_text('title = "热重载"\n', encoding="utf-8")
    (root / "plugin.py").write_text(
        "from pathlib import Path\n"
        "from app.pluginkit.registry import Extension\n"
        "HERE = Path(__file__).parent\n"
        "(HERE / 'loads.txt').open('a', encoding='utf-8').write('x')\n"
        "class Hot(Extension):\n"
        "    name = 'hot'\n"
        "    def on_stop(self):\n"
        "        (HERE / 'stopped.txt').write_text('stopped', encoding='utf-8')\n"
        "def setup(ctx):\n"
        "    return Hot()\n",
        encoding="utf-8",
    )

    client, _ = _client(tmp_path, plugin_dir=tmp_path / "plugins")
    with client:
        enabled = client.post("/api/plugins/hot/enabled", json={"enabled": True})
        assert enabled.status_code == 200, enabled.text
        loads = root / "loads.txt"
        assert loads.read_text(encoding="utf-8") == "x"

        # 重载必须重新执行模块（含子模块缓存要清掉），并先跑 on_stop
        reloaded = client.post("/api/plugins/hot/reload")
        assert reloaded.status_code == 200, reloaded.text
        data = reloaded.json()
        assert data["ok"] is True, data
        assert loads.read_text(encoding="utf-8") == "xx"
        assert (root / "stopped.txt").exists()
        item = next(p for p in data["plugins"] if p["name"] == "hot")
        assert item["enabled"] is True

        # 停用也要走 on_stop，而不是把扩展悄悄丢掉
        (root / "stopped.txt").unlink()
        off = client.post("/api/plugins/hot/enabled", json={"enabled": False})
        assert off.status_code == 200, off.text
        assert (root / "stopped.txt").exists()


def _make_plugin(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "plugin.toml").write_text(f'title = "{name}"\n', encoding="utf-8")
    (folder / "plugin.py").write_text("def setup(ctx):\n    return None\n", encoding="utf-8")
    return folder


def test_plugin_list_has_stable_name_order_and_install_time(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    _make_plugin(plugin_dir, "zeta")
    _make_plugin(plugin_dir, "alpha")
    client, _ = _client(tmp_path, plugin_dir=plugin_dir)
    with client:
        items = client.get("/api/plugins").json()["plugins"]
        assert [item["name"] for item in items] == ["alpha", "zeta"]
        assert all(item["installed_at"] > 0 for item in items)


def test_plugin_delete_keeps_data_by_default(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    folder = _make_plugin(plugin_dir, "del")
    data_dir = tmp_path / "plugin_data" / "del"
    data_dir.mkdir(parents=True)
    (data_dir / "settings.toml").write_text("x = 1\n", encoding="utf-8")

    client, _ = _client(tmp_path, plugin_dir=plugin_dir)
    with client:
        response = client.delete("/api/plugins/del")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert not folder.exists()
        assert (data_dir / "settings.toml").exists()
        assert all(item["name"] != "del" for item in payload["plugins"])
        assert payload["data_removed"] is False


def test_plugin_delete_with_data_and_enabled_cleanup(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    _make_plugin(plugin_dir, "del")
    data_dir = tmp_path / "plugin_data" / "del"
    data_dir.mkdir(parents=True)
    (data_dir / "settings.toml").write_text("x = 1\n", encoding="utf-8")

    client, config_path = _client(tmp_path, plugin_dir=plugin_dir)
    with client:
        enabled = client.post("/api/plugins/del/enabled", json={"enabled": True})
        assert enabled.status_code == 200, enabled.text
        assert 'enabled = ["del"]' in config_path.read_text(encoding="utf-8")

        response = client.delete("/api/plugins/del?data=1")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data_removed"] is True
        assert not data_dir.exists()
        assert "enabled = []" in config_path.read_text(encoding="utf-8")

        unknown = client.delete("/api/plugins/nope")
        assert unknown.status_code == 404


def _zip_bytes(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_plugin_zip_install(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    payload = _zip_bytes(
        {
            "packed/plugin.toml": 'title = "打包插件"\n',
            "packed/plugin.py": "def setup(ctx):\n    return None\n",
            "packed/README.md": "# 打包说明\n",
        }
    )
    client, path = _client(tmp_path, plugin_dir=plugin_dir)
    with client:
        response = client.post(
            "/api/plugins/install",
            content=payload,
            headers={"Content-Type": "application/zip"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True
        assert (plugin_dir / "packed" / "plugin.toml").exists()
        item = next(p for p in data["plugins"] if p["name"] == "packed")
        assert item["enabled"] is True
        assert "启用" in data["message"]

        readme = client.get("/api/plugins/packed/readme")
        assert readme.status_code == 200
        assert "打包说明" in readme.json()["text"]

        again = client.post(
            "/api/plugins/install",
            content=payload,
            headers={"Content-Type": "application/zip"},
        )
        assert again.status_code == 409

    assert "packed" in load_config(path).extensions.enabled


def test_plugin_zip_root_layout_uses_zip_name(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    payload = _zip_bytes(
        {
            "plugin.toml": 'title = "根布局"\n',
            "plugin.py": "def setup(ctx):\n    return None\n",
            "pages/index.html": "<!doctype html><h1>root</h1>",
            "README.md": "# 根目录说明\n",
            "__MACOSX/._plugin": "junk",
        }
    )
    client, _ = _client(tmp_path, plugin_dir=plugin_dir)
    with client:
        response = client.post(
            "/api/plugins/install?name=root-pack.zip",
            content=payload,
            headers={"Content-Type": "application/zip"},
        )
        assert response.status_code == 200, response.text
        assert (plugin_dir / "root_pack" / "plugin.toml").exists()
        assert (plugin_dir / "root_pack" / "pages" / "index.html").exists()
        item = next(p for p in response.json()["plugins"] if p["name"] == "root_pack")
        assert item["enabled"] is True


def test_plugin_zip_root_layout_uses_manifest_id(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    payload = _zip_bytes(
        {
            "plugin.toml": 'id = "manifest_pack"\ntitle = "清单 id"\n',
            "plugin.py": "def setup(ctx):\n    return None\n",
        }
    )
    client, _ = _client(tmp_path, plugin_dir=plugin_dir)
    with client:
        # 没有文件名提示也能装：id 以 plugin.toml 为准
        response = client.post(
            "/api/plugins/install",
            content=payload,
            headers={"Content-Type": "application/zip"},
        )
        assert response.status_code == 200, response.text
        assert (plugin_dir / "manifest_pack" / "plugin.toml").exists()


def test_plugin_zip_id_must_match_directory(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    payload = _zip_bytes(
        {
            "wrong/plugin.toml": 'id = "other"\ntitle = "错位"\n',
            "wrong/plugin.py": "def setup(ctx):\n    return None\n",
        }
    )
    client, _ = _client(tmp_path, plugin_dir=plugin_dir)
    with client:
        response = client.post(
            "/api/plugins/install",
            content=payload,
            headers={"Content-Type": "application/zip"},
        )
        assert response.status_code == 400
        assert "id 与目录名不一致" in response.json()["message"]
        assert not (plugin_dir / "wrong").exists()


def test_plugin_zip_root_layout_requires_name(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    payload = _zip_bytes(
        {
            "plugin.toml": 'title = "没有名字"\n',
            "plugin.py": "def setup(ctx):\n    return None\n",
        }
    )
    client, _ = _client(tmp_path, plugin_dir=plugin_dir)
    with client:
        response = client.post(
            "/api/plugins/install",
            content=payload,
            headers={"Content-Type": "application/zip"},
        )
        assert response.status_code == 400
        assert "ZIP 命名" in response.json()["message"]


def test_plugin_zip_rejects_ambiguous_layout(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    payload = _zip_bytes(
        {
            "plugin.toml": 'title = "根"\n',
            "inner/plugin.toml": 'title = "里"\n',
            "inner/plugin.py": "def setup(ctx):\n    return None\n",
        }
    )
    client, _ = _client(tmp_path, plugin_dir=plugin_dir)
    with client:
        response = client.post(
            "/api/plugins/install?name=ambiguous.zip",
            content=payload,
            headers={"Content-Type": "application/zip"},
        )
        assert response.status_code == 400
        assert "结构不明确" in response.json()["message"]


def test_plugin_zip_rejects_unsafe_paths_and_links(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    base = {"packed/plugin.toml": 'title = "坏包"\n', "packed/plugin.py": "def setup(ctx):\n    return None\n"}

    client, _ = _client(tmp_path, plugin_dir=plugin_dir)
    with client:
        slip = client.post(
            "/api/plugins/install",
            content=_zip_bytes({**base, "../evil.txt": "x"}),
            headers={"Content-Type": "application/zip"},
        )
        assert slip.status_code == 400
        assert not (tmp_path / "evil.txt").exists()

        absolute = client.post(
            "/api/plugins/install",
            content=_zip_bytes({**base, "/abs.txt": "x"}),
            headers={"Content-Type": "application/zip"},
        )
        assert absolute.status_code == 400

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in base.items():
                archive.writestr(name, content)
            link = zipfile.ZipInfo("packed/link")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            archive.writestr(link, "/etc/passwd")
        symlink = client.post(
            "/api/plugins/install",
            content=buffer.getvalue(),
            headers={"Content-Type": "application/zip"},
        )
        assert symlink.status_code == 400

        two_roots = client.post(
            "/api/plugins/install",
            content=_zip_bytes(
                {
                    "a/plugin.toml": 'title = "A"\n',
                    "a/plugin.py": "def setup(ctx):\n    return None\n",
                    "b/plugin.toml": 'title = "B"\n',
                    "b/plugin.py": "def setup(ctx):\n    return None\n",
                }
            ),
            headers={"Content-Type": "application/zip"},
        )
        assert two_roots.status_code == 400

        missing = client.post(
            "/api/plugins/install",
            content=_zip_bytes({"packed/plugin.py": "def setup(ctx):\n    return None\n"}),
            headers={"Content-Type": "application/zip"},
        )
        assert missing.status_code == 400


def test_plugin_app_version_warning(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    for name, app_version in (
        ("oldver", ">=999"),
        ("okver", ">=0.0"),
        ("barever", "0.0"),
        ("badver", "banana"),
    ):
        folder = plugin_dir / name
        folder.mkdir()
        (folder / "plugin.toml").write_text(
            f'title = "{name}"\napp_version = "{app_version}"\n', encoding="utf-8"
        )
        (folder / "plugin.py").write_text("def setup(ctx):\n    return None\n", encoding="utf-8")

    client, _ = _client(tmp_path, plugin_dir=plugin_dir)
    with client:
        items = {item["name"]: item for item in client.get("/api/plugins").json()["plugins"]}
    assert items["oldver"]["error"] is None
    assert "要求 qq-bot-lite >=999" in items["oldver"]["warning"]
    assert items["okver"]["warning"] == ""
    assert items["barever"]["warning"] == ""
    assert "写法不合法" in items["badver"]["warning"]


def test_plugin_reload_unknown(tmp_path: Path) -> None:
    (tmp_path / "plugins").mkdir()
    client, _ = _client(tmp_path, plugin_dir=tmp_path / "plugins")
    with client:
        response = client.post("/api/plugins/nope/reload")
        assert response.status_code == 404


def test_plugin_readme_and_missing(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, plugin_dir=FIXTURE_PLUGINS)
    with client:
        page = client.get("/api/plugins/demo/readme")
        assert page.status_code == 200, page.text
        data = page.json()
        assert data["ok"] is True
        assert "测试用的" in data["text"]
        assert data["filename"].lower() == "readme.md"
        assert data["truncated"] is False
        # Markdown 必须渲染，而不是把原文塞进 <pre>
        assert "<h1>" in data["html"]
        assert "<strong>README</strong>" in data["html"]
        assert "<table>" in data["html"]
        assert 'class="language-python"' in data["html"]

    # 没有 README 的插件：404 + 人话，不是 500
    root = tmp_path / "plugins" / "noreadme"
    root.mkdir(parents=True)
    (root / "plugin.toml").write_text('title = "没有文档"\n', encoding="utf-8")
    (root / "plugin.py").write_text("def setup(ctx):\n    return None\n", encoding="utf-8")
    client, _ = _client(tmp_path, plugin_dir=tmp_path / "plugins")
    with client:
        missing = client.get("/api/plugins/noreadme/readme")
        assert missing.status_code == 404
        assert "README" in missing.json()["message"]
        unknown = client.get("/api/plugins/nope/readme")
        assert unknown.status_code == 404


def test_plugin_readme_sanitizes_html(tmp_path: Path) -> None:
    root = tmp_path / "plugins" / "unsafe"
    root.mkdir(parents=True)
    (root / "plugin.toml").write_text('title = "不安全"\n', encoding="utf-8")
    (root / "plugin.py").write_text("def setup(ctx):\n    return None\n", encoding="utf-8")
    (root / "README.md").write_text(
        "# 标题\n\n"
        "<script>alert(1)</script>\n"
        "<img src=x onerror=alert(2)>\n"
        "[坏链接](javascript:alert(3))\n",
        encoding="utf-8",
    )

    client, _ = _client(tmp_path, plugin_dir=tmp_path / "plugins")
    with client:
        html = client.get("/api/plugins/unsafe/readme").json()["html"].lower()
    assert "<script" not in html
    assert "<img" not in html
    assert "onerror" not in html
    assert "javascript:" not in html
    assert "<h1>标题</h1>" in html


def test_plugin_readme_truncates_large_file(tmp_path: Path) -> None:
    from app.web.plugin_routes import README_LIMIT

    root = tmp_path / "plugins" / "huge"
    root.mkdir(parents=True)
    (root / "plugin.toml").write_text('title = "大文档"\n', encoding="utf-8")
    (root / "plugin.py").write_text("def setup(ctx):\n    return None\n", encoding="utf-8")
    (root / "README.md").write_text("A" * (README_LIMIT + 10), encoding="utf-8")

    client, _ = _client(tmp_path, plugin_dir=tmp_path / "plugins")
    with client:
        data = client.get("/api/plugins/huge/readme").json()
        assert data["ok"] is True
        assert data["truncated"] is True
        assert len(data["text"]) == README_LIMIT


def test_plugin_settings_roundtrip(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, plugin_dir=FIXTURE_PLUGINS)
    with client:
        response = client.post(
            "/api/plugins/demo/settings",
            json={"values": {"window_hours": 48, "show_latency": False}},
        )
        assert response.status_code == 200, response.text
        saved = response.json()["settings"]
        assert saved["window_hours"] == 48
        assert saved["show_latency"] is False

        bad = client.post("/api/plugins/demo/settings", json={"values": {"window_hours": 99999}})
        assert bad.status_code == 400

    # 落在配置文件同目录的 plugins.toml 里
    text = (tmp_path / "plugin_data" / "demo" / "settings.toml").read_text(encoding="utf-8")
    assert "window_hours = 48" in text
    assert "show_latency = false" in text


def test_plugin_toggle_writes_config(tmp_path: Path) -> None:
    client, path = _client(tmp_path, plugin_dir=FIXTURE_PLUGINS)
    with client:
        response = client.post("/api/plugins/demo/enabled", json={"enabled": True})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        demo = next(item for item in payload["plugins"] if item["name"] == "demo")
        assert demo["enabled"] is True
        assert 'enabled = ["demo"]' in path.read_text(encoding="utf-8")

        off = client.post("/api/plugins/demo/enabled", json={"enabled": False})
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
    for line in ("model =", "base_url =", "timeout ="):
        assert line in template_block and line in copy_block
    assert "max_concurrency =" not in template_block
    assert "max_concurrency =" not in copy_block


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

def test_plugin_api_too_new_is_rejected(tmp_path: Path) -> None:
    """插件声明的 api 高于本机支持，必须显示成错误卡而不是硬加载。"""
    plugin_dir = tmp_path / "plugins" / "future"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text('title = "未来插件"\napi = 99\n', encoding="utf-8")
    (plugin_dir / "plugin.py").write_text("def setup(ctx):\n    return None\n", encoding="utf-8")

    client, _ = _client(tmp_path, plugin_dir=tmp_path / "plugins")
    with client:
        plugins = client.get("/api/plugins").json()["plugins"]
        item = next(p for p in plugins if p["name"] == "future")
        assert item["error"] and "api" in item["error"]


def test_plugin_package_import_and_page(tmp_path: Path) -> None:
    """入口按包加载：能 from .core 导入；目录里有 pages/index.html 就能访问。"""
    root = tmp_path / "plugins" / "demo_pkg"
    (root / "core").mkdir(parents=True)
    (root / "pages").mkdir()
    (root / "plugin.toml").write_text('title = "包插件"\npanel = "/plugins/demo_pkg"\n', encoding="utf-8")
    (root / "core" / "__init__.py").write_text("", encoding="utf-8")
    (root / "core" / "greet.py").write_text('WORD = "hello"\n', encoding="utf-8")
    (root / "pages" / "index.html").write_text("<html><body>demo page</body></html>", encoding="utf-8")
    (root / "plugin.py").write_text(
        "from app.pluginkit.registry import Extension\n"
        "from .core.greet import WORD\n\n"
        "class Demo(Extension):\n"
        "    name = 'demo_pkg'\n"
        "    async def system_prompt(self, ctx):\n"
        "        return WORD\n\n"
        "def setup(ctx):\n"
        "    return Demo()\n",
        encoding="utf-8",
    )

    client, _ = _client(tmp_path, plugin_dir=tmp_path / "plugins")
    with client:
        toggled = client.post("/api/plugins/demo_pkg/enabled", json={"enabled": True})
        assert toggled.status_code == 200, toggled.text
        page = client.get("/plugins/demo_pkg/")
        assert page.status_code == 200 and "demo page" in page.text


def test_plugin_settings_fields_hook(tmp_path: Path) -> None:
    """settings_fields 能改字段（比如把输入框换成动态下拉），保存仍走同一份字段。"""
    root = tmp_path / "plugins" / "dyn"
    root.mkdir(parents=True)
    (root / "plugin.toml").write_text(
        'title = "动态设置"\n'
        '[[config]]\nkey = "model"\nlabel = "模型"\ntype = "text"\ndefault = ""\n',
        encoding="utf-8",
    )
    (root / "plugin.py").write_text(
        "from app.pluginkit.registry import Extension\n\n"
        "class Dyn(Extension):\n"
        "    name = 'dyn'\n"
        "    async def settings_fields(self, ctx, fields):\n"
        "        for field in fields:\n"
        "            if field['key'] == 'model':\n"
        "                field['type'] = 'options'\n"
        "                field['options'] = ['', 'a-model']\n"
        "                field['labels'] = ['跟主模型一致', 'a-model']\n"
        "        return fields\n\n"
        "def setup(ctx):\n"
        "    return Dyn()\n",
        encoding="utf-8",
    )

    client, _ = _client(tmp_path, plugin_dir=tmp_path / "plugins")
    with client:
        client.post("/api/plugins/dyn/enabled", json={"enabled": True})
        data = client.get("/api/plugins/dyn/settings").json()
        assert data["ok"], data
        field = next(item for item in data["fields"] if item["key"] == "model")
        assert field["type"] == "options"
        assert field["options"] == ["", "a-model"]

        saved = client.post("/api/plugins/dyn/settings", json={"values": {"model": "a-model"}})
        assert saved.status_code == 200, saved.text
        assert saved.json()["settings"]["model"] == "a-model"


def test_plugin_settings_can_be_empty(tmp_path: Path) -> None:
    """返回空列表 = 设置全在自己页面里，主面板不给通用表单。"""
    root = tmp_path / "plugins" / "own"
    root.mkdir(parents=True)
    (root / "plugin.toml").write_text(
        'title = "自带界面"\n'
        '[[config]]\nkey = "secret"\nlabel = "密钥"\ntype = "text"\ndefault = ""\n',
        encoding="utf-8",
    )
    (root / "plugin.py").write_text(
        "from app.pluginkit.registry import Extension\n\n"
        "class Own(Extension):\n"
        "    name = 'own'\n"
        "    async def settings_fields(self, ctx, fields):\n"
        "        return []\n\n"
        "def setup(ctx):\n"
        "    return Own()\n",
        encoding="utf-8",
    )

    client, _ = _client(tmp_path, plugin_dir=tmp_path / "plugins")
    with client:
        client.post("/api/plugins/own/enabled", json={"enabled": True})
        data = client.get("/api/plugins/own/settings").json()
        assert data["ok"], data
        assert data["fields"] == []


def test_plugin_hook_error_is_surfaced(tmp_path: Path) -> None:
    """钩子抛异常要被记到 last_error，而不是只写日志。"""
    root = tmp_path / "plugins" / "boom"
    root.mkdir(parents=True)
    (root / "plugin.toml").write_text('title = "会炸的插件"\n', encoding="utf-8")
    (root / "plugin.py").write_text(
        "from app.pluginkit.registry import Extension\n\n"
        "class Boom(Extension):\n"
        "    name = 'boom'\n"
        "    async def on_message(self, ctx):\n"
        "        raise RuntimeError('炸了')\n\n"
        "def setup(ctx):\n"
        "    return Boom()\n",
        encoding="utf-8",
    )

    client, _ = _client(tmp_path, plugin_dir=tmp_path / "plugins")
    with client:
        client.post("/api/plugins/boom/enabled", json={"enabled": True})
        registry = client.app.state.plugin_registry
        from app.pluginkit.registry import HookContext

        hook = HookContext(event=None, session_key="group:1", is_group=True)
        client.portal.call(registry.on_message, hook)
        plugins = client.get("/api/plugins").json()["plugins"]
        item = next(p for p in plugins if p["name"] == "boom")
        assert item["last_error"], "钩子异常没有反映到面板数据里"


