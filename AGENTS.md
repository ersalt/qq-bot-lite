# AGENTS.md · qq-bot-lite

给接手这个仓库的 AI / 新同事看的。读完这份就知道：项目在哪、怎么跑、哪些设计不能碰。

## 这是什么

一个自己写的 QQ 机器人：OneBot 11 **反向 WS 服务端**（SnowLuma 连进来），
后端 FastAPI + SQLite，模型走本机 LM Studio 的 OpenAI 兼容接口。
定位是「为本地部署反着做」——KV 缓存、显存、并发槽位是一等公民。取舍见 `docs/local-first.md`。

## 路径与仓库

```
D:\Dev\qq-bot-lite       本仓库（Python 3.12，venv 在 .venv）
GitHub: ersalt/qq-bot-lite
  main     已发布（v0.3.4）
  develop  开发主线（领先 main 的提交数按 git 实际为准）
```

- 版本号唯一来源：根目录 `VERSION`。改它必须同步改 `CHANGELOG.md` 的标题（`## vX.Y.Z — 日期`），
  否则 `tests/test_version.py` 会挂。
- 分支规范：日常在 `develop`；发版改版本号 + CHANGELOG → `git merge --no-ff develop` → 附注 tag → Release。

## 怎么跑

```powershell
# 启动（会开一个控制台窗口）
Start-Process explorer.exe -ArgumentList 'D:\Dev\qq-bot-lite\start.cmd'

# 优雅停机（进程 exit 0，窗口自己关）
Invoke-WebRequest -Method POST -Uri http://127.0.0.1:8790/api/shutdown

# 面板
http://127.0.0.1:8790/
```

- 日志：`logs/app-YYYYMMDD-HHMMSS.log`（每次启动一个），`logs/latest.txt` 指向最新；
  保留最近 30 次启动，控制台同一份格式（见 `docs/log-spec.md`）。
- 配置：`config/config.toml`（**不入库**，首次启动从 `config.example.toml` 复制）。
- 数据：`data/app.db`（会话）、`data/plugin_data/<插件id>/`（插件数据）、`data/images/`。

## 校验（改完必须跑）

```powershell
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\ruff.exe check .
.venv\Scripts\python.exe -m app.pluginkit.check plugins
```

CI 是 3.12/3.13 × Ubuntu/Windows 的矩阵，上面三条 + 插件自检。

## 架构地图

```
app/
  core/pipeline.py    消息主链路：判定 → 拼上下文 → 调模型 → 分段发送
  core/tool_runner.py model-facing 工具循环：tool_calls → 执行 → role=tool → 最终正文
  llm/client.py       LM Studio 客户端（complete / complete_message / stream）
  context/store.py    SQLite 会话库、上下文裁剪
  pluginkit/          插件运行时：清单、发现、加载、钩子、工具、设置、自检
  web/                FastAPI 路由 + 静态前端（page.html / app.js / panels.js / plugins.js）
  config.py           dataclass 配置 + TOML 读写（写回保留注释）
plugins/              插件（只从这里加载；_template 是骨架）
personas/             人设 Markdown
docs/                 guide.md（用户指南）、settings.md（设置说明）、development.md（开发文档）、
                      api.md（SDK/API）、plugin-integration.md（内置插件协作）、plugin.md（插件开发）、
                      local-first.md、log-spec.md、ui-spec.md；WebUI「文档」页签本地阅读。
                     鲸鱼空间自己的设计记录在 plugins/bot_space/docs/，主项目不再重复一份
```

## 不能碰的设计

1. **system 提示词必须逐字稳定。** 时间、群名、说话人这些动态信息拼在**当前 user 消息**后面
   （插件的 `user_suffix` 钩子），不进 system。system 改一个字，KV 缓存整段失效，
   1600 token 多花 2~3 秒。
2. **同一时间只向模型发一条请求**（`[llm] max_concurrency = 1`）。多槽位并行会让 KV 缓存互相失效，
   生成速度从 62 t/s 掉到 22 t/s。
3. **没有构建链。** 前端是纯原生 JS + 本地内置的 [Basecoat](https://basecoatui.com)（`static/basecoat.css`）。
   按钮/输入框走它的类名和 `data-variant`；默认态**不要**写 `data-variant="default"`（会匹配不到、变透明）。
   表单下拉框用 `uiSelect`（`static/ui.js`），不要手写原生 `<select>`。
   **界面规范在 `docs/ui-spec.md`，改界面前先读；令牌只准在 `theme.css` 定义，`style.css` 只引用。**
4. **插件只从项目内 `plugins/` 加载**，`[extensions] dirs` 留空。`whale_lexicon` 和
   `bot_space` 在主仓库有完整内置副本（含 tests/docs，随项目分发、方便学习）；主要开发仍在
   各自插件项目里进行，完成后同步到 `plugins/<id>/`。新插件也可以直接放 `plugins/<id>/`。
5. 配置写回是**逐行改**，不是整份重写——注释必须保留。

## 插件模型（细节见 docs/plugin.md）

- 契约版本 `api = 2`：`plugin.toml`（元数据 + `[[config]]` 设置项）+ `plugin.py` 的 `setup(ctx)`。
- 钩子：`on_message` / `user_suffix` / `system_prompt` / `after_reply` / `settings_fields`，另有
  `on_start` / `on_stop` 生命周期。钩子在消息主链路上，必须快进快出（5ms 量级）。
- 工具：插件导出 `TOOLS`（或重写 `tools()`），宿主把 schema 交给模型，`ToolRunner` 执行
  `tool_calls` 并回填 `role=tool`；工具对话不落持久历史。`api=1` 老插件继续兼容。
- `ctx` 提供 `settings` / `data_dir` / `services`（llm, store, service, cfg）/ `spawn` / `log`。
- 异常隔离：任一钩子抛异常只记 `last_error`，不影响聊天。
- 面板可看 README、热重载、删除（可选连 `data/plugin_data/<id>/` 一起）、安装 ZIP
  （只收 `<插件id>/plugin.toml` 单顶层目录，8MB/64MB 上限，装完默认启用）。
- 内置插件与主项目的边界、宿主请求和交接格式见 `docs/plugin-integration.md`；
  插件改 UI 前先走这份约定。

## 常见坑

- **改版本号忘了改 CHANGELOG 标题** → `test_changelog_has_entry_for_current_version` 挂。
- **上下文窗口取数必须用 `latest_after()`**：`after()` 是最早优先，历史一超过上限
  最新对话就进不了窗口，模型会“三句就忘”。回归测试见 `tests/test_context_window.py`。
- **历史配对必须用 `assistant.reply_to`**：不要用“紧挨着 assistant 的 user”，群里插话
  会把回复算到别人头上；老数据没有 reply_to 时才退回相邻逻辑。见 `app/context/window.py`。
- **别把「（回 ...）」写进 assistant 内容**：模型会照抄成回复格式；历史里不写，`clean_text`
  和 `_render` 负责清掉旧数据/漏出来的前缀。
- **群聊闲话不塞持久历史**：用 `_ambient_context` 取“上次 assistant 之后”的消息，作为
  `<system_reminder>` 注入当前 user 内容；持久历史只留 reply_to 配对的问答。
- **引用消息要带原文**：`get_msg` 取发送者/文本/图片，放 `<Quoted Message>`；只靠
  “他引用了你”模型猜不到指的是哪句。见 `pipeline._quoted_info`。
- **`body` 不能设 `overflow-x: hidden`**：它会变成没有滚动范围的滚动容器，滚轮被吃掉，
  整页滚不动（进阶设置展开后最明显）。`html`/`body` 一律 `overflow-x: clip`。
- **下拉框统一用 `uiSelect`**：原生 `<select>` 在弹窗里没法做浮层/勾选，而且 Basecoat 的 preflight
  会把原生控件边框抹掉。`uiSelect` 在弹窗内会把浮层挂到 `dialog` 上，避免被 `overflow` 裁切。
- **通知统一走 `toast()`**：主面板顶部 `.toast-stack`，弹窗打开时自动挂到该 `dialog` 里；不要再写
  底部 `.toast`（旧样式只留给插件页）。新建重名这类表单错误同时给行内 `.err`，不能只靠通知。
- **工具和命令是两套东西**：`COMMANDS` 是用户输入 `/xxx`；`TOOLS` 是模型自己调用的工具。
  工具对话只在本次请求里，**不落持久历史**；工具对所有会话暴露，不按群聊/私聊分流。
  工具执行必须走 `ToolRunner`，不要绕过 `_llm_slot` 直接并发调模型。
- `python -m app.pluginkit.check` 在 GBK 控制台会因 `✓` 编码报错：先
  `$env:PYTHONIOENCODING = "utf-8"` 再跑（不是代码问题）。
- **移动目录 / 重启前先停进程**，否则 `.venv`、`data/*.db` 被占用。
- `start.cmd` 里是 `timeout` 不是 `pause`：优雅退出（exit 0）时窗口自己关，强杀最多留 10 秒。
- 面板静态资源走 `/static/<name>`，`routes.py` 会给它加 `?v=<mtime>`；**放在子目录里匹配不到**，
  所以 basecoat 的文件是平的（`static/basecoat.css`）。
- 截图：Chrome headless，`--screenshot` 必须用**绝对路径**；截图前可临时注入脚本把状态栏 QQ 号
  替换成占位值（截完 `git checkout -- app/web/page.html`）。

## 周边目录

```
D:\Dev\
  qq-bot-lite\              本项目
  whale_lexicon\            旧插件源码仓库（已内置进本仓库，保留只作历史参考）
  astrbot_plugin_slang\     AstrBot 版插件，收尾维护中（跟本项目无关）
  _archive\                 归档：bot-workspace-legacy（AstrBot 时代的工作区）、
                            astrbot_plugin_no_send_tool（已报废）
  _lab\ _thirdparty\ _tools\ 其它杂物，跟本项目无关
```

历史工作区 `D:\Dev\bot-workspace` 已于 2026-11-04 归档到 `_archive\bot-workspace-legacy\`
（含 git bundle 和全部文件）。那里面是 AstrBot 时代的知识库和排查手册，**不适用于本项目**。

## 当前状态（2026-11-09）

- `v0.3.4` 已发布（2026-11-09）：启动脚本检测并修复缺失的 `.venv`/依赖；
  内置鲸语汇 `0.6.0`、鲸鱼空间 `0.5.5`。上一个正式版 `v0.3.3`：
  https://github.com/ersalt/qq-bot-lite/releases/tag/v0.3.3
- 面板：顶栏 + 页头 + 页内页签 + 白卡行式；页签最后是「文档」「关于」；
  设置小字只保留必要短 hint，配置 key 标签已移除；弹窗有打开/退场动画。
- 文档：guide / settings / development / api / plugin-integration 入口；
  local-first / plugin / log-spec / ui-spec 作为深入参考；WebUI「文档」页签读本地 Markdown。
- 本机运行时：机器人在跑，LM Studio 加载 `qwen3.8-27b`（10240 ctx，parallel 1），SnowLuma 反连 8790。
- 插件：`whale_lexicon 0.6.0` / `bot_space 0.5.5` 在主仓库有完整内置副本（tests/docs）；
  主要开发仍在各自插件项目，按 `docs/plugin-integration.md` 同步。
