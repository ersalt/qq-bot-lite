# 插件

插件放在项目根的 `plugins/`，每个插件一个文件夹；运行期数据写到
`data/plugin_data/<插件名>/`。两边分开放，升级或搬家时不会互相踩。

- 规范（目录结构、日志、依赖、发布清单）：[plugin-spec.md](plugin-spec.md)
- 接口签名（钩子、命令、面板）：[plugin-api.md](plugin-api.md)
- 骨架：复制 `plugins/_template/`，改个名就能开工
- 自检：`.venv\Scripts\python.exe -m app.pluginkit.check plugins/<名字>`
- 代码风格：`.venv\Scripts\python.exe -m ruff check .`（与主程序同一份 `ruff.toml` 规则）
- 样式：页面直接引 `/static/basecoat.css` + `/static/theme.css`（Basecoat 组件库，本地内置、无外部请求）

```
plugins/
  stats/                 一个插件一个目录，目录名就是插件名
    plugin.toml          元数据和设置项声明（不导入代码就能读）
    plugin.py            代码：setup(ctx) 和可选的 router
data/
  plugin_data/
    stats/
      settings.toml      设置面板写的那几个键
      counter.json       插件自己存的东西
```

和 AstrBot 一样的分法：插件目录归插件，数据目录归运行期。
**插件本体入库，`data/plugin_data/` 里除了 `settings.toml` 一律不入库。**

## 最小插件

`plugins/ping/plugin.toml`：

```toml
title = "回音"
description = "被叫到的时候顺带回一句。"
version = "0.1.0"
author = "你"
```

`plugins/ping/plugin.py`：

```python
from app.pluginkit.manifest import PluginContext
from app.pluginkit.registry import Extension, HookContext


class Ping(Extension):
    name = "ping"

    async def system_prompt(self, ctx: HookContext):
        return None


def setup(ctx: PluginContext) -> Ping:
    return Ping()
```

放进 `plugins/`，设置面板的「插件」页就会多一张卡，不用注册、不用重启配置。

## plugin.toml 的全部键

| 键 | 作用 |
|---|---|
| `title` | 卡片标题，缺省用目录名 |
| `description` | 卡片说明，最多显示三行 |
| `version` / `author` | 卡片上的小字 |
| `panel` | 有值才显示「打开面板」，一般写 `/plugins/<名字>` |
| `[[config]]` | 有值才显示「设置」，每个表就是一个字段 |

`[[config]]` 的字段：

```toml
[[config]]
key = "window_hours"        # 存进 settings.toml 的键名
label = "统计窗口（小时）"
type = "number"             # number / bool / text
min = 1
max = 720
step = 1
default = 24
help = "面板上那句“最近 N 小时”就是它"
```

**元数据刻意放在 TOML 而不是代码里**：面板列一遍插件时不会执行任何插件代码，
装坏的插件只会显示成一张红卡并写明原因，不会拖垮整个列表。

## 代码约定

| 名字 | 必需 | 说明 |
|---|---|---|
| `setup(ctx)` | 否 | 返回扩展实例；返回 `None` 也合法（只有面板没有钩子的插件） |
| `router` | 否 | 一个 `APIRouter`，挂到 `/plugins/<名字>` |

`ctx` 是 `PluginContext`：

- `ctx.name`：插件名
- `ctx.settings`：`settings.toml` 合并默认值后的字典，改设置会同步刷新
- `ctx.data_dir`：`data/plugin_data/<名字>/`，已经建好，随你写
- `ctx.services`：`{"store": 消息库, "service": ChatService, "cfg": 配置}`

给得少是故意的。插件想要更多能力，说明那东西该做成核心功能。

## 钩子

继承 `Extension` 并实现任意一个：

| 钩子 | 时机 | 用途 |
|---|---|---|
| `on_message(ctx)` | 每条消息判定完触发之后、回复之前 | 计数、学习、记录 |
| `system_prompt(ctx)` | 拼 system 提示词时 | 返回一段文本，追加到 system 末尾 |
| `after_reply(ctx, text)` | 文本要发出去之前 | 改写、过滤 |

`ctx` 是 `HookContext`，带 `event`（消息事件）、`session_key`、`is_group`、`history`。
**钩子抛异常只记日志，绝不影响聊天主流程。**

## 启停

面板上的开关等价于把名字加进 / 移出 `config/config.toml` 的 `[extensions] enabled`。
启用和停用都是热生效：钩子立刻挂上或摘掉，面板路由同时挂载或撤掉，不用重启进程。

## 去哪看真例子

- `tests/fixtures/plugins/demo/`：最小可运行插件，`plugin.py` + `plugin.toml` 十来行，
  适合拿来抄骨架。
- `tests/test_web.py` 里的 `test_plugin_package_import_and_page`：带 `core/` 子包和
  `pages/index.html` 的写法长什么样，看这一段最直接。
- 真在线上跑的那个是 `whale_lexicon`（独立仓库，在 `config/config.toml` 的
  `[extensions] dirs` 里登记）：侧栏 + 列表 + 批量操作 + 弹窗的完整面板，
  接口统一按 `{ok, message}` 返回，可以直接对着改。

面板页想跟主面板同款，`<link rel="stylesheet" href="/static/style.css">` 就能复用全部
色彩令牌和控件样式。
