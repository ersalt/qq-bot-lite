# 插件模板（_template）

复制整个目录、改目录名，就是新插件的起点。目录名以 `_` 开头，加载器会跳过它，
所以放在 `plugins/` 里也不会真的被启用。

```powershell
Copy-Item -Recurse plugins/_template plugins/my_plugin
```

然后改三处：

1. `plugin.toml`：`title` / `description` / `version` / `panel`（目录名变了，面板路径也要改）
2. `plugin.py`：`class Template.name` 改成插件目录名
3. `pages/index.html`：标题和要展示的数据

跑一下自检，确认没漏东西：

```powershell
.venv\Scripts\python.exe -m app.pluginkit.check plugins/my_plugin
```

规范全文见 `docs/plugin-spec.md`。
