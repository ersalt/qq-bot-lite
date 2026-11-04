# 插件开发

插件是 qq-bot-lite 的扩展方式：一个文件夹、一份清单、一个 `setup()`，就能挂上钩子、
命令、设置和网页面板。本文既是**接口契约**，也是**写作规范**——两者冲突时，以本文的
「必须」为准。

当前接口版本：`api = 1`

自检：

```powershell
.venv\Scripts\python.exe -m app.pluginkit.check plugins/你的插件
```

---

## 1. 五分钟上手

```powershell
Copy-Item -Recurse plugins/_template plugins/my_plugin
.venv\Scripts\python.exe -m app.pluginkit.check plugins/my_plugin
```

改 `plugin.toml` 里的 `title` / `description`，再把 `plugin.py` 的 `setup()` 填上，
重启进程，主面板「插件」里就能看到。

最小可运行的插件只要两个文件：

```toml
# plugins/my_plugin/plugin.toml
title = "我的插件"
description = "一句话说明"
version = "0.1.0"
```

```python
# plugins/my_plugin/plugin.py
from app.sdk import Extension, HookContext, PluginContext


class MyPlugin(Extension):
    name = "my_plugin"

    async def on_message(self, hook: HookContext) -> None:
        pass


def setup(ctx: PluginContext) -> MyPlugin:
    return MyPlugin()
```

---

## 2. 目录结构

```
plugins/<id>/
  plugin.toml      必需，元数据 + 设置项（不导入代码就能读）
  plugin.py        必需（可用 entry 改名），导出 setup(ctx)
  pages/index.html 可选，面板页面；写了 panel 就必须有
  core/            可选，纯逻辑，不 import app.*
  tools/           可选，运维脚本（导入导出之类）
  tests/           可选，pytest，用主程序同一个 venv
  README.md        建议，用法 + 截图
  CHANGELOG.md     建议，按版本记
  docs/            可选，截图等
```

- 目录名就是插件 id：`[A-Za-z0-9_]+`，会出现在 URL、配置键和日志里。别用中文或横线。
- `_` 或 `.` 开头的目录**不会被加载**，可以放模板、废弃代码。
- 运行期数据一律写 `ctx.data_dir`（`data/plugin_data/<id>/`），别写进插件目录。

---

## 3. 清单：plugin.toml

```toml
title = "鲸语汇"                   # 必需，面板和日志里显示的名字
description = "一句话说明"
version = "0.4.1"                 # SemVer x.y.z
author = "贰叔"

api = 1                           # 插件接口版本，默认 1；比主程序新的会拒绝加载
entry = "plugin.py"               # 可选，默认 plugin.py
panel = "/plugins/whale_lexicon"  # 可选，写了才显示「打开面板」
app_version = ">=0.2"             # 可选，不满足只警告，不拦加载

[[config]]                        # 可选，有值才显示「设置」
key = "enabled"                   # 存进 data/plugin_data/<id>/settings.toml 的键
label = "总开关"
type = "bool"                     # bool / number / text / options
default = true
help = "说明文字"

# 分组：同 group 的字段排在一块；不写 group 的单独成块（无标题）
group = "basic"
group_label = "基础"

# 条件显示：依赖项变了就跟着显示/隐藏，值仍照常保存
show_if = { key = "wiki_enable", value = true }

# number 的上下限，超了会被拒绝
min = 0
max = 100

# text 渲染成多行输入框（JSON 之类的长文本）
multiline = true

# options：下拉框，值和显示名分开给
options = ["auto", "off", "custom"]
labels = ["自动识别", "保持思考", "自定义"]
```

**元数据与代码分开是硬要求**：列插件列表时绝不执行插件代码。写坏的插件只能显示成
一张带错误的卡，不能把整个列表拖垮。

字段不够用时实现 `settings_fields(ctx, fields)` 在运行时改写（见第 5 节）。

---

## 4. 入口与生命周期

```python
from app.sdk import Extension, HookContext, PluginContext

class MyPlugin(Extension):
    name = "my_plugin"                # 与目录名一致，出错时用它定位

    async def on_start(self): ...     # 可选：加载完成后调一次
    async def on_stop(self): ...      # 可选：进程退出时收尾

def setup(ctx: PluginContext) -> MyPlugin:
    return MyPlugin()

COMMANDS = []                         # 可选，见第 6 节
router = APIRouter()                  # 可选，挂在 /plugins/<id>/ 下
```

- 返回 `None` 合法：只有面板、没有钩子的插件。
- **只准 `from app.sdk import ...`**；`app.pluginkit.*`、`app.core.*` 属于内部实现，会变。
- 入口按包的方式加载，`from .core import xxx` 可用。
- `setup` 里不要做慢操作（读大文件、发网络请求），它会卡启动。要预热就丢 `ctx.spawn`。

| 时机 | 做什么 |
|---|---|
| 加载时 | `setup(ctx)`：建对象、开库，**别起副作用** |
| 启用后 | `on_start()`：起后台任务、预热缓存 |
| 停用/退出 | `on_stop()`：自己收尾；`ctx.spawn` 的任务会被统一取消 |

热启用/停用会重新加载插件：`setup` 会再跑一次，别把状态只放内存里。

**后台任务请用 `ctx.spawn(coro)`**，不要自己 `create_task`——自己起的任务在退出时
没人收，容易卡住关闭流程。

---

## 5. 钩子

| 钩子 | 时机 | 能改什么 |
|---|---|---|
| `on_message(hook)` | 每条消息判定完、回复前 | 只观察 |
| `user_suffix(hook)` | 拼当前用户消息时 | 返回一段文本，追加到这条消息末尾 |
| `system_prompt(hook)` | 拼 system 时 | 返回一段文本，追加到 system 末尾 |
| `after_reply(hook, text)` | 文本发出前 | 返回改写后的文本 |
| `settings_fields(ctx, fields)` | 打开设置弹窗时 | 返回新字段列表，改渲染 |

**注入优先用 `user_suffix` 而不是 `system_prompt`**：system 是静态的，模型能一直命中
KV 缓存；塞进 system 会让缓存从注入点断开，每条消息都要重算前面的历史。只有「永远都在、
且不随消息变化」的内容才适合放 system。

**钩子在消息主链路上，必须快进快出**：

- 不做网络请求、不调模型、不 sleep。要做得起任务，把结果放内存/库里。
- 每个钩子 5 毫秒量级；超过 50ms 就该考虑挪走。
- 抛异常会被记进 `last_error` 并打日志，**不影响聊天**，但等于这个钩子废了。

`HookContext`：

| 字段 | 说明 |
|---|---|
| `event` | 消息事件：`sender_name`、`user_id`、`group_id`、`content.text`、`content.images` |
| `session_key` | 会话键，形如 `group:123456789`，跨钩子稳定 |
| `is_group` | 群聊还是私聊 |
| `history` | 已拼好的历史（只读） |

**顺序**：按 `[extensions] enabled` 里的顺序，`system_prompt` / `after_reply` 依次串起来。
别假设自己是唯一的插件。

**`settings_fields` 是设置面板的兜底渲染接口**：入参是 `plugin.toml` 声明的那份字段，
返回新列表就按新的渲染，返回 `None` 或抛异常就用原样：

```python
async def settings_fields(self, ctx, fields):
    models = await ctx.services["llm"].list_models()   # 运行时才知道的选项
    for field in fields:
        if field["key"] == "learn_model" and models:
            field["type"] = "options"
            field["options"] = [""] + models
            field["labels"] = ["跟主模型一致"] + models
    return fields
```

清单里的声明是**兜底**（模型服务连不上，照旧是输入框），这条钩子是**增强**。
设置全在自己页面里画的话，直接返回 `[]`，主面板就不显示通用表单，只留「打开插件页面」。

---

## 6. 插件能拿到什么

`setup(ctx)` 的参数 `PluginContext`：

| 属性 | 说明 |
|---|---|
| `ctx.name` | 插件 id |
| `ctx.title` | 清单里的标题，日志和面板上显示的名字 |
| `ctx.settings` | `settings.toml` 合并默认值后的字典；面板改完会同步刷新 |
| `ctx.data_dir` | `data/plugin_data/<id>/`，已创建，随便写 |
| `ctx.services` | 下面这张表 |
| `ctx.panel_url` | 有 `panel` 时的路径，否则 `None` |
| `ctx.spawn(coro)` | 起一个受管的后台任务 |
| `ctx.log` | 日志：自动加 `[标题]` 前缀，logger 名 `plugin.<id>` |

`ctx.services` 目前提供：

| key | 类型 | 用途 |
|---|---|---|
| `llm` | `LLMClient` | `await llm.complete(messages)` / `stream(...)`，与机器人同一套模型配置 |
| `store` | `MessageStore` | 主消息库（只读用，别往里乱写） |
| `service` | `ChatService` | 运行时计数、`stats_line` 之类 |
| `cfg` | `Config` | 主配置（只读） |

**故意给得少**。插件想做别的事，说明那个能力该做成机器人核心功能，而不是每个插件
自己去摸内部。

---

## 7. 命令

插件导出 `COMMANDS`，机器人接管 `/前缀 命令名 参数`：

```python
from app.sdk import Command, CommandContext

async def handle(cmd: CommandContext) -> str:
    return "收到：" + " ".join(cmd.args)

COMMANDS = [Command("mycmd", handler=handle, help="说明", admin=True)]
```

- 命中命令**不进模型、不进历史**，直接回文本；没命中的 `/xxx` 照常走触发流程。
- 命令名小写字母数字，别跟别的插件撞（撞了后加载的覆盖先加载的，日志里会警告）。
- `admin=True` 只允许 `[commands] admins` 里的 QQ 号；留空表示不限制（本地自用方便，
  群里建议填上）。
- **单个命令 20 秒超时**，慢活丢后台；抛异常只回一句「命令执行失败」。
- 子命令自己拆：`/yh approve x` 这种由插件按 `cmd.args` 处理。

---

## 8. 网页面板

- `pages/index.html` 是单文件，访问 `/plugins/<id>/` 返回它。
- 需要接口就导出 `router = APIRouter()`，挂在 `/plugins/<id>/` 下。
- **页面里的接口用相对路径**（`./list`），别写死 `/plugins/xxx`。
- 统一返回 `{"ok": bool, "message": str, ...}`，4xx 也要带 `message`。
- 面板不做鉴权，和主面板同权限：**只在本机监听前提下使用**。

样式：面板可以引主程序的静态资源，跟主面板长得一模一样：

```html
<link rel="stylesheet" href="/static/basecoat.css">
<link rel="stylesheet" href="/static/style.css">
<link rel="stylesheet" href="/static/theme.css">
```

组件库是 [Basecoat](https://basecoatui.com)（MIT，本地内置、无外部请求）。按钮用
`class="btn"` + `data-variant="outline|ghost|destructive"` + `data-size="sm|icon"`，
输入框 `class="input"`、下拉 `class="select"`、文本域 `class="textarea"`、
开关用 `<input type="checkbox" role="switch" class="input">`，弹窗用原生
`<dialog class="dialog">`。主题跟着 `data-theme` 走。

这些是主程序的静态资源，跨大版本可能调整；插件要绝对独立就自己写样式。
面板里想直接跳去自己的设置：链 `/?plugin=<id>#plugins`，会打开那个插件的设置弹窗。

### 8.1 安装 / 文档 / 删除 / 重载

主面板的插件页现在可以直接管理插件：

- **文档**：读插件根目录的 `README.md`（大小写不敏感，`readme.markdown` / `.txt` 也行），
  超过 256KB 截断；弹窗按原文展示，不渲染 HTML。
- **安装 ZIP**：点「安装 ZIP」先开拖放弹窗，把包拖进去或点拖放区选文件；压缩包里只能有
  一个顶层目录，结构是 `<插件id>/plugin.toml`。拒绝绝对路径、`..`、反斜杠、符号链接、加密包；
  压缩包 ≤8MB，解压后 ≤64MB。装完默认**不启用**。
- **删除**：先卸载，再把插件目录改名成隐藏临时目录清理；可选连
  `data/plugin_data/<id>/` 一起删。不勾“不可恢复”点不了删除。
- **重载**：重扫 `plugin.toml`、重跑 `setup()`，会先调 `on_stop()` 并取消 `ctx.spawn()`
  起的任务；`pages/` 和 `router` 的增删仍要重启进程才彻底生效。

所以插件自己的 `README.md` 最好写清楚设置项和依赖；持久化数据一律放
`ctx.data_dir`，用户才会被明确告知“删插件时要不要连数据一起删”。

---

## 9. 日志

统一用 `ctx.log`，它自动加 `[插件标题]` 前缀，落到 `plugin.<id>` 这个 logger 上：

```python
ctx.log.info("注入 %s", "黑话 梁文峰")
# 控制台：22:00:22  [鲸语汇] 注入 黑话 梁文峰
# 文件：  22:00:22 INFO    plugin.whale_lexicon: [鲸语汇] 注入 黑话 梁文峰
```

| 级别 | 用在哪 |
|---|---|
| `info` | 用户关心的事件：注入、学习完成、同步结果、启用/停用 |
| `warning` | 能自愈但要看一眼：模型超时、接口 429、配置写错 |
| `error` | 这轮没救了：数据库写失败、依赖服务连不上 |
| `debug` | 只在排错时要的细节：每条消息的中间状态 |

- 用中文，一句话说清「发生了什么 + 关键数字」：`学习完成：3 批，入库 5 条`。
- **别逐条打聊天内容**，也别打完整 prompt、API key、群友隐私。长文本自己截断到 120 字。
- 不要手写 `[插件名]` 前缀，`ctx.log` 已经加了。
- 模块级 `logging.getLogger(__name__)` 只用于排错细节，用户可见的事件都走 `ctx.log`。

---

## 10. 数据与配置的边界

| 东西 | 放哪 | 谁写 |
|---|---|---|
| 插件开关 | `config/config.toml` 的 `[extensions] enabled` | 主面板 |
| 插件设置 | `data/plugin_data/<id>/settings.toml` | 面板 / 手改 |
| 插件数据 | `data/plugin_data/<id>/` | 插件自己 |

- 设置只用 `plugin.toml` 里声明过的键。想存别的，写到 `ctx.data_dir`，别往
  `settings.toml` 里塞未声明的键——面板会按声明重写这个文件。
- 数据库开 `WAL`，并发读写不互相卡。
- 升级要兼容旧库：用 `PRAGMA table_info` 补列，别 `DROP TABLE`。

---

## 11. 依赖与安全

依赖：

- 只用**主程序已装的包** + 标准库。插件不跑 `pip install`，也别带 wheel。
- 需要新依赖：先在主程序里加，再在 `app_version` 里写明要求。
- 不要求跨平台，但别写死 `D:\` 这种绝对路径（自检会警告）。

安全——插件是**进程内代码**，没有沙箱：和机器人同一个 Python 进程、同样的权限。
「装插件」等于「信任作者」，这条要写进使用说明里。

对插件作者：

- 只从可信来源拿插件；README 里写清「它读了什么、写了什么、连了哪些网」。
- 密钥只放 `ctx.settings`，不写进代码、不贴进日志。
- 对外请求加超时；用户内容（聊天、昵称、头像）不要外发到没说明的第三方。
- 文件读写限定在 `ctx.data_dir`；别去读主程序的 `config/config.toml`、`data/app.db`。
- 数据库操作走参数化查询，别拼 SQL。

对使用者：

- 装新插件前扫一眼 `plugin.py` 里的 `import` 和外链域名。
- 面板没有账号体系，**只在本机监听时用**；别把 8790 暴露到公网。

---

## 12. 版本与兼容

- `version` 语义化（SemVer）：数据/接口不兼容升 `x`，加功能升 `y`，修 bug 升 `z`。
- `api` 是插件接口版本，主程序只加载 `api <= 自己支持的最高版本` 的插件；高了就显示成
  错误卡，写明「需要更新的机器人」。
- 对新主程序的要求写进 `app_version`（如 `">=0.2"`），不满足只警告、不拦加载。
- **废弃策略**：`api` 升版后旧接口至少保留一个大版本；删之前先在 `CHANGELOG` 标
  「deprecated」，日志里对调用方打一次 warning。
- 插件之间**不要互相 import**。要共享逻辑，各自实现或由机器人提供能力。
- `CHANGELOG` 按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)：`## x.y.z`
  + 无序列表，说人话，不贴 commit 列表。提交信息用 Conventional Commits。

---

## 13. 工具链、CI 与发布

本地三件事，跟主程序同一套：

```powershell
python -m ruff check .                    # 静态检查（E/W/F/B/I，行长 120）
python -m pytest tests -q                 # 单元测试
python -m app.pluginkit.check <插件目录>   # 规范自检
```

CI 至少跑这个矩阵，插件和主程序一样不能只在一个 Python 上验过：

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, windows-latest]
    python: ["3.12", "3.13"]
```

发布前清单：

- [ ] `app.pluginkit.check` 没有 `×`（错误）
- [ ] `ruff check .` 干净，`pytest` 全绿
- [ ] 面板能打开、接口 200，设置保存后重启还在
- [ ] 停用 → 启用，钩子不重复挂；进程退出没有残留任务
- [ ] 断网 / 模型挂掉 / 数据库锁住，插件不把主流程拖死
- [ ] 日志里没有 API key、完整聊天记录
- [ ] `README` 有用法和截图，`CHANGELOG` 有这一版，`LICENSE` 在

---

## 14. 模板

`plugins/_template/` 是最小可运行骨架（清单、钩子、接口、面板、命令、日志全都有），
复制改名即可：

```powershell
Copy-Item -Recurse plugins/_template plugins/my_plugin
.venv\Scripts\python.exe -m app.pluginkit.check plugins/my_plugin
```

---

## 15. 明确不做的事

- 不做插件沙箱：插件是可信代码，和机器人同进程同权限。
- 不做插件间依赖 / 事件总线：真需要时再议。
- 不做热重载单个插件：改代码就重启进程，简单可靠。
- 不做子命令自动解析：子命令由插件自己按 `cmd.args` 拆。
