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
  main     已发布（v0.2.0）
  develop  开发主线，当前比 main 多 3 个提交
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
  llm/client.py       LM Studio 客户端（complete / stream）
  context/store.py    SQLite 会话库、上下文裁剪
  pluginkit/          插件运行时：清单、发现、加载、钩子、设置、自检
  web/                FastAPI 路由 + 静态前端（page.html / app.js / panels.js / plugins.js）
  config.py           dataclass 配置 + TOML 读写（写回保留注释）
plugins/              插件（只从这里加载；_template 是骨架）
personas/             人设 Markdown
docs/                 local-first.md（定位与实测）、plugin.md（插件开发）、log-spec.md（日志格式）、
                      ui-spec.md（界面契约：令牌/骨架/组件/动效/验收）
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
4. **插件只从项目内 `plugins/` 加载**，`[extensions] dirs` 留空。插件是独立仓库，源码在项目外，
   部署副本放在 `plugins/<id>/`（主仓库 `.gitignore` 已忽略，副本里有 `.deployed.json` 记录版本提交）。
5. 配置写回是**逐行改**，不是整份重写——注释必须保留。

## 插件模型（细节见 docs/plugin.md）

- 契约版本 `api = 1`：`plugin.toml`（元数据 + `[[config]]` 设置项）+ `plugin.py` 的 `setup(ctx)`。
- 钩子：`on_message` / `user_suffix` / `system_prompt` / `after_reply` / `settings_fields`，另有
  `on_start` / `on_stop` 生命周期。钩子在消息主链路上，必须快进快出（5ms 量级）。
- `ctx` 提供 `settings` / `data_dir` / `services`（llm, store, service, cfg）/ `spawn` / `log`。
- 异常隔离：任一钩子抛异常只记 `last_error`，不影响聊天。
- 面板可看 README、热重载、删除（可选连 `data/plugin_data/<id>/` 一起）、安装 ZIP
  （只收 `<插件id>/plugin.toml` 单顶层目录，8MB/64MB 上限，装完默认不启用）。

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
  whale_lexicon\            插件源码仓库（独立发版，部署副本在 plugins/whale_lexicon）
  astrbot_plugin_slang\     AstrBot 版插件，收尾维护中（跟本项目无关）
  _archive\                 归档：bot-workspace-legacy（AstrBot 时代的工作区）、
                            astrbot_plugin_no_send_tool（已报废）
  _lab\ _thirdparty\ _tools\ 其它杂物，跟本项目无关
```

历史工作区 `D:\Dev\bot-workspace` 已于 2026-11-04 归档到 `_archive\bot-workspace-legacy\`
（含 git bundle 和全部文件）。那里面是 AstrBot 时代的知识库和排查手册，**不适用于本项目**。

## 当前状态（2026-11-04）

- `v0.2.0` 已发布：https://github.com/ersalt/qq-bot-lite/releases/tag/v0.2.0
- 面板已按 `docs/ui-spec.md` 重排：顶栏 + 页头 + 页内页签 + 白卡行式，动效见规范第 7 节。
- 文档四份：local-first / plugin / log-spec / ui-spec。
- 本机运行时：机器人在跑，LM Studio 加载 `qwen3.8-27b`（32768 ctx），SnowLuma 反连 8790。
- 插件部署模型：源码 `D:\Dev\whale_lexicon` → `tools/deploy-to-bot.ps1` → `plugins/whale_lexicon`。
