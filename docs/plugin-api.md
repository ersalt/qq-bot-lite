# 插件接口约定（v1）

本文件是插件与机器人之间的**契约**。改这里的任何一条，都属于破坏性变更，
必须升 `api` 版本号，并在 CHANGELOG 里写清楚迁移方法。

「一个插件该长什么样」（目录结构、日志、依赖、发布清单）见 **[plugin-spec.md](plugin-spec.md)**；
本文只讲接口签名。

当前：`api = 1`

---

## 1. 插件是什么

`plugins/` 下（或 `[extensions] dirs` 登记的目录下）**一个文件夹就是一个插件**，
文件夹名同时是插件 id，也是 `[extensions] enabled` 里写的名字。

```
plugins/whale_lexicon/
  plugin.toml      元数据与设置项（必需）
  plugin.py        代码入口（必需，可用 entry 改）
  pages/index.html 可选：面板页面
  core/            可选：自己的子模块
```

id 必须是 `[A-Za-z0-9_]+`：它会出现在 URL、配置键、日志里，别用中文或横线。

---

## 2. plugin.toml

```toml
title = "鲸语汇"                    # 必需，面板上显示的名字
description = "一句话说明"
version = "0.1.0"
author = "贰叔"

api = 1                             # 可选，默认 1。高于机器人支持的版本会拒绝加载
entry = "plugin.py"                 # 可选，默认 plugin.py
panel = "/plugins/whale_lexicon"    # 可选，有值才显示「打开面板」
app_version = ">=0.2"               # 可选，不满足只警告，不拦加载

[[config]]                          # 可选，有值才显示「设置」
key = "auto_learn"                  # 存进 data/plugin_data/<id>/settings.toml 的键
label = "自动学习"
type = "bool"                       # bool / number / text / options
default = true
help = "说明文字"
group = "learning"                  # 可选，设置弹窗按这个分组
group_label = "自动学习"             # 可选，分组的标题
show_if = { key = "wiki_enable", value = true }   # 可选，依赖另一个开关
min = 0                             # number 类型的上下限，超了会被拒绝
max = 100

# type = "text" 时：multiline = true 渲染成多行输入框（JSON 之类的长文本）
# type = "options" 时：下拉框，值和显示名分开给
options = ["auto", "off", "custom"]
labels = ["自动识别", "保持思考", "自定义"]
```

`group` 相同的字段会落在一块，没写 `group` 的单独成块（无标题）。
`show_if` 依赖的键变化时，字段会跟着显示/隐藏，值仍然照常保存。
下拉框配 `show_if` 正好能做「选到自定义才显示 JSON 输入框」。

**字段不够用时**：插件可以实现 `settings_fields(ctx, fields)` 这个兜底接口，
在运行时改写字段（补动态选项、按状态隐藏）。见第 6 节。

**元数据与代码分开**是硬要求：列插件时绝不能执行插件代码，
写坏的插件只能显示成一张带错误的卡，不能把列表拖垮。

---

## 3. 代码入口

`plugin.py` 至少导出 `setup(ctx)`：

```python
from app.pluginkit.registry import Extension, HookContext
from app.pluginkit.manifest import PluginContext

class MyPlugin(Extension):
    name = "my_plugin"

    async def on_message(self, hook: HookContext) -> None: ...
    async def system_prompt(self, hook: HookContext) -> str | None: ...
    async def after_reply(self, hook: HookContext, text: str) -> str: ...

def setup(ctx: PluginContext) -> MyPlugin:
    return MyPlugin()
```

- 返回 `None` 合法：只有面板、没有钩子的插件。
- **能 import 兄弟模块**：入口按包的方式加载，`from .core import xxx` 可用
  （入口文件所在目录就是一个包）。
- 也能 `import` 标准库和机器人已装的第三方库；**不要 import `app.*` 的私有模块**，
  需要什么能力就往 `ctx` 上加（见第 5 节）。

---

## 4. 生命周期

```python
class MyPlugin(Extension):
    async def on_start(self) -> None: ...   # 可选：加载完成后调一次
    async def on_stop(self) -> None: ...    # 可选：进程关闭时调，用来收尾
```

**后台任务请用 `ctx.spawn(coro)`**，不要自己 `create_task`：

```python
async def on_start(self):
    self.ctx.spawn(self._loop())        # 进程退出时会被统一取消
```

自己起的任务在退出时没人收，容易卡住关闭流程。

---

## 5. 插件能拿到什么

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
| `ctx.log` | 日志：自动加 `[标题]` 前缀，logger 名 `plugin.<id>`（写法见 plugin-spec「日志」） |

`ctx.services` 目前提供：

| key | 类型 | 用途 |
|---|---|---|
| `llm` | `LLMClient` | `await llm.complete(messages)` / `stream(...)`，与机器人同一套模型配置 |
| `store` | `MessageStore` | 主消息库（只读用，别往里乱写） |
| `service` | `ChatService` | 运行时计数、`stats_line` 之类 |
| `cfg` | `Config` | 主配置（只读） |

**故意给得少**。插件想做别的事，说明那个能力该做成机器人核心功能，
而不是每个插件自己摸内部。

---

## 6. 钩子

| 钩子 | 时机 | 能不能改结果 |
|---|---|---|
| `on_message(hook)` | 每条消息判定完触发、回复之前 | 不能，只观察 |
| `system_prompt(hook)` | 拼 system 提示词时 | 返回一段文本，追加到 system 末尾 |
| `user_suffix(hook)` | 拼当前这条用户消息时 | 返回一段文本，追加到这条消息末尾 |
| `settings_fields(ctx, fields)` | 打开插件设置弹窗时 | 返回新的字段列表，改渲染 |
| `after_reply(hook, text)` | 文本要发出去之前 | 返回改写后的文本 |

**注入优先用 `user_suffix` 而不是 `system_prompt`**：system 是静态的，模型能一直命中
KV 缓存；塞进 system 会让缓存从注入点断开，每条消息都要重新算一遍前面那些历史。
只有「永远都在、且不随消息变化」的内容才适合放 system。

**`settings_fields` 是设置面板的兜底渲染接口**：入参是 `plugin.toml` 里声明的那份字段，
返回新的列表就按新的渲染，返回 `None` 或抛异常就用原样。典型用法：

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

清单里的声明是**兜底**（接口挂了、模型服务连不上，照旧是输入框），
这条钩子是**增强**。真想完全自定义布局，就在插件自己的 `pages/index.html` 里画，
设置弹窗会带一个「打开插件页面」的入口；如果设置全在页面里，`settings_fields`
直接返回 `[]`，主面板就不显示通用表单了，只留那个入口。

`HookContext`：

| 字段 | 说明 |
|---|---|
| `event` | 消息事件：`sender_name`、`user_id`、`group_id`、`content.text`、`content.images` |
| `session_key` | 会话键，形如 `group:1058912272`，跨钩子稳定 |
| `is_group` | 群聊还是私聊 |
| `history` | 已拼好的历史（只读） |

**顺序**：按 `[extensions] enabled` 里的顺序，`system_prompt` / `after_reply`
会依次串起来。别假设自己是唯一的插件。

**异常隔离是硬保证**：任何一个钩子抛异常，只记日志并把该插件标记为「出过错」，
其他插件和主流程不受影响。

---

## 7. 网页面板

- 插件目录下放 `pages/index.html`，访问 `/plugins/<id>/` 就会返回它。
- 需要接口就导出 `router = APIRouter()`，挂在 `/plugins/<id>/` 下。
- **页面的接口前缀自己算**：用 `location.pathname` 的相对路径，
  或者把 `ctx.panel_url` 写进模板；不要写死 `/plugins/xxx`。
- 面板不做鉴权，和机器人面板同权限：**只在本机监听前提下使用**。
- 想跟主面板长得一模一样，直接引 `/static/style.css`：色彩令牌、按钮、输入框、
  弹窗、toast 都在里面，主题跟着 `data-theme` 走。它属于主程序的静态资源，
  跨大版本可能调整；插件要绝对独立就自己写样式。
- 面板里想跳去自己的设置：链 `/?plugin=<id>#plugins`，会直接打开那个插件的设置弹窗。

---

## 8. 数据与配置的边界

| 东西 | 放哪 | 谁写 |
|---|---|---|
| 插件开关 | `config/config.toml` 的 `[extensions] enabled` | 面板 |
| 插件设置 | `data/plugin_data/<id>/settings.toml` | 面板 / 手改 |
| 插件数据 | `data/plugin_data/<id>/` | 插件自己 |

设置只用 `plugin.toml` 里声明过的键。想存别的东西，写到 `data_dir` 里自己管，
别往 `settings.toml` 里塞未声明的键——面板会按声明重写这个文件。

---

## 9. 版本与兼容

- 插件声明 `api`，机器人只加载 `api <= 自己支持的最高版本` 的插件；
  高于就显示成错误卡，写明「需要更新的机器人」。
- 机器人升级后如果要改契约，老 `api` 至少保留一个版本周期。
- 插件之间**不要互相 import**。要共享逻辑，各自实现或由机器人提供能力。

---

## 10. 明确不做的事（避免过度设计）

- 不做插件沙箱：插件是可信代码，和机器人同进程同权限。
- 不做插件间依赖 / 事件总线：真需要时再议。
- 不做热重载单个插件：改代码就重启进程，简单可靠。
- 不做子命令自动解析：`/yh approve x` 这种，子命令由插件自己按 `cmd.args` 拆。


---

## 11. 命令

插件导出 `COMMANDS`，机器人接管 `/前缀 命令名 参数`：

```python
from app.sdk import Command, CommandContext

async def handle(cmd: CommandContext) -> str:
    return "收到 " + " ".join(cmd.args)

COMMANDS = [Command("hello", handler=handle, help="示例", admin=False)]
```

- 命中命令**不进模型、不进历史**，直接回文本；没命中的 `/xxx` 照常走触发流程。
- `admin=True` 的命令只允许 `[commands] admins` 里的 QQ 号执行；
  这个列表留空表示不限制（本地自用方便，群里建议填上）。
- handler 超时由 `[commands] timeout` 兜底，抛异常只回一句「命令执行失败」，不影响别的插件。
- 插件之间的命令重名：后加载的覆盖先加载的，日志里会有警告。
