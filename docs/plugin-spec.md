# 插件规范（v1）

这份文档说「一个插件长什么样」，`plugin-api.md` 说「接口怎么调」。
两者冲突时以本文为准（本文是约定，API 文档是签名）。

自检：

```powershell
.venv\Scripts\python.exe -m app.pluginkit.check plugins/你的插件
```

---

## 1. 目录结构

```
plugins/<id>/
  plugin.toml          必需，元数据 + 设置项声明（不导入代码就能读）
  plugin.py            必需（可用 entry 改名），导出 setup(ctx)
  pages/index.html     可选，面板页面；写了 panel 就必须有
  core/                可选，纯逻辑，不 import app.*
  tools/               可选，运维脚本（导入导出之类）
  tests/               可选，pytest，用主程序同一个 venv
  README.md            建议，用法 + 截图
  CHANGELOG.md         建议，按版本记
  docs/                可选，截图等
```

- 目录名就是插件 id：`[A-Za-z0-9_]+`，会出现在 URL、配置键、日志里。
- `_` 或 `.` 开头的目录**不会被加载**，可以放模板、废弃代码。
- 运行期数据一律写 `ctx.data_dir`（`data/plugin_data/<id>/`），别写到插件目录里。

---

## 2. 清单：plugin.toml

```toml
title = "鲸语汇"            # 必需，面板和日志里显示的名字
description = "一句话说明"
version = "0.3.3"          # x.y.z
author = "贰叔"

api = 1                    # 插件接口版本，默认 1；比主程序新的会拒绝加载
entry = "plugin.py"        # 可选
panel = "/plugins/whale_lexicon"   # 可选，写了才显示「打开面板」
app_version = ">=0.2"      # 可选，不满足只警告

[[config]]                 # 可选，有值才显示「设置」
key = "enabled"
label = "总开关"
type = "bool"              # bool / number / text / options
default = true
help = "说明文字"
group = "basic"            # 分组；同组会排在一块
group_label = "基础"
show_if = { key = "wiki_enable", value = true }   # 条件显示
min = 0                    # number 的上下限
max = 100
multiline = true           # text 渲染成多行
options = ["auto", "off"]  # options 的候选值
labels = ["自动", "关闭"]   # options 的显示名
```

**元数据与代码分开是硬要求**：列插件列表时绝不执行插件代码。

---

## 3. 代码入口

```python
from app.sdk import Extension, HookContext, PluginContext

class MyPlugin(Extension):
    name = "my_plugin"          # 与目录名一致，出错时用它定位
    async def on_start(self): ...
    async def on_stop(self): ...

def setup(ctx: PluginContext) -> MyPlugin:
    return MyPlugin()

COMMANDS = []                    # 可选
router = APIRouter()             # 可选，挂在 /plugins/<id>/ 下
```

- **只准 `from app.sdk import ...`**；`app.pluginkit.*`、`app.core.*` 属于内部实现，会变。
- `core/` 里的纯逻辑模块不要 import `app.*`，需要机器人能力就由 `plugin.py` 传进去。
- `setup` 里不要做慢操作（读大文件、发网络请求），它会卡启动。要预热就丢 `ctx.spawn`。

### 生命周期

| 时机 | 做什么 |
|---|---|
| 加载时 | `setup(ctx)` → 建对象、开库；**别起副作用** |
| 启用后 | `on_start()`：起后台任务（`ctx.spawn`）、预热缓存 |
| 停用/退出 | `on_stop()`：自己收尾；`ctx.spawn` 的任务会被统一取消 |

热启用/停用会重新加载插件：重启后 `setup` 会再跑一次，别把状态只放内存里。

---

## 4. 钩子

| 钩子 | 时机 | 能改什么 |
|---|---|---|
| `on_message(ctx)` | 每条消息判定完、回复前 | 只观察 |
| `user_suffix(ctx)` | 拼当前用户消息时 | 返回一段文本，追加到这条消息末尾 |
| `system_prompt(ctx)` | 拼 system 时 | 返回一段文本，追加到 system 末尾 |
| `after_reply(ctx, text)` | 文本发出前 | 返回改写后的文本 |
| `settings_fields(ctx, fields)` | 打开设置弹窗时 | 返回新字段列表 |

**钩子在消息主链路上，必须快进快出。** 规则：

- 不做网络请求、不调模型、不 sleep。要做得起任务、把结果放内存/库里。
- 每个钩子 5 毫秒量级；超过 50ms 就该考虑挪走。
- 抛异常会被记进 `last_error` 并打日志，**不影响聊天**，但等于这个钩子废了。

`ctx.log` 之外的日志（模块级 `logging.getLogger(__name__)`）只用于排错细节，
用户可见的事件都走 `ctx.log`。

---

## 5. 日志

统一用 `ctx.log`，它自动加 `[插件标题]` 前缀，落到 `plugin.<id>` 这个 logger 上：

```python
ctx.log.info("注入 %s", "黑话 梁文峰")
# 控制台：22:00:22  [鲸语汇] 注入 黑话 梁文峰
# 文件：  22:00:22 INFO    plugin.whale_lexicon: [鲸语汇] 注入 黑话 梁文峰
```

约定：

| 级别 | 用在哪 |
|---|---|
| `info` | 用户关心的事件：注入、学习完成、同步结果、启用/停用 |
| `warning` | 能自愈但要看一眼：模型超时、接口 429、配置写错 |
| `error` | 这轮没救了：数据库写失败、依赖服务连不上 |
| `debug` | 只在排错时要的细节：每条消息的中间状态 |

内容：

- 用中文，一句话说清「发生了什么 + 关键数字」：`学习完成：3 批，入库 5 条`。
- **别逐条打聊天内容**，也别打完整 prompt、API key、群友隐私。
- 长文本自己截断到 120 字以内。
- 不要手写 `[插件名]` 前缀，`ctx.log` 已经加了。

---

## 6. 数据与配置

| 东西 | 放哪 | 谁写 |
|---|---|---|
| 插件开关 | `config/config.toml` 的 `[extensions] enabled` | 主面板 |
| 插件设置 | `data/plugin_data/<id>/settings.toml` | 面板 / 手改 |
| 插件数据 | `data/plugin_data/<id>/` | 插件自己 |

- 设置只用 `plugin.toml` 里声明过的键；想存别的，写到 `ctx.data_dir`。
- 数据库开 `WAL`，并发读写不互相卡。
- 升级要兼容旧库：用 `PRAGMA table_info` 补列，别 `DROP TABLE`。

---

## 7. 面板

- `pages/index.html` 是单文件，访问 `/plugins/<id>/` 返回它。
- 接口用 `router`，挂在 `/plugins/<id>/` 下；**页面里用相对路径**（`./list`），别写死绝对路径。
- 样式直接引 `/static/style.css`，主题跟着 `data-theme` 走。
- 统一返回 `{"ok": bool, "message": str, ...}`，4xx 也要带 `message`。
- 面板不做鉴权，和主面板同权限：**只在本机监听时用**。

设置界面有两个入口，**读写同一份数据**：

1. 主面板「插件」→「设置」，按 `[[config]]` 渲染
2. 插件页面自己画，调 `/api/plugins/<id>/settings`（GET 拿字段和值，POST 保存）

字段不够用时实现 `settings_fields(ctx, fields)` 改字段（补动态下拉、按状态隐藏），
返回 `[]` 表示「设置全在我自己页面里」。

---

## 8. 命令

```python
from app.sdk import Command, CommandContext

async def handle(cmd: CommandContext) -> str:
    return "收到：" + " ".join(cmd.args)

COMMANDS = [Command("mycmd", handler=handle, help="说明", admin=True)]
```

- 命令名小写字母数字，别跟别的插件撞（撞了会互相覆盖，日志里会警告）。
- 返回字符串就是回给群里的文本；**单个命令 20 秒超时**，慢活丢后台。
- 命令不经过模型，也不进上下文历史。

---

## 9. 依赖

- 只用**主程序已装的包** + 标准库。插件不跑 `pip install`，也别带 wheel。
- 需要新依赖：先在主程序里加，再在 `app_version` 里写明要求。
- 不要求跨平台，但别写死 `D:\` 这种绝对路径（自检会警告）。

---

## 10. 版本与兼容

- `version` 语义化：`x.y.z`；数据/接口不兼容就升 `x`。
- `api` 是插件接口版本，主程序只加载 `api <= 自己支持的最高版本` 的插件。
- 插件对主程序的新要求写进 `app_version`（如 `">=0.2"`），不满足只警告、不拦加载。

---

## 11. 发布前清单

- [ ] `python -m app.pluginkit.check plugins/<id>` 没有 `×`（错误）
- [ ] 面板能打开、接口 200，设置保存后重启还在
- [ ] 停用 → 启用，钩子不重复挂；进程退出没有残留任务
- [ ] 断网 / 模型挂掉 / 数据库锁住，插件不把主流程拖死
- [ ] 日志里没有 API key、完整聊天记录
- [ ] `README` 有用法和截图，`CHANGELOG` 有这一版

---

## 12. 模板

`plugins/_template/` 是最小可运行骨架（清单、钩子、接口、面板、命令、日志全都有），
复制改名即可：

```powershell
Copy-Item -Recurse plugins/_template plugins/my_plugin
.venv\Scripts\python.exe -m app.pluginkit.check plugins/my_plugin
```
