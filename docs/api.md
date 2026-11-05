# SDK 与 API 列表

> 这篇是速查表。插件开发的完整规则看[插件规范](plugin.md)，项目内部链路看[开发文档](development.md)。
>
> 插件开发主要在自己的插件项目里进行；主仓库 `plugins/<id>/` 是随项目分发的完整内置副本
> （含 tests/docs，方便其他人学习），改完后由插件作者同步进来。

## 1. 插件 SDK（`app.sdk`）

插件**只准 import `app.sdk`**，不要直接 import `app.pluginkit.*` / `app.core.*`。
`api = 2` 是当前契约。

```python
from app.sdk import (
    API_VERSION,
    Command,
    CommandContext,
    Extension,
    HookContext,
    PluginContext,
    Tool,
    ToolContext,
)
```

### 1.1 `Extension`

插件的入口类；所有钩子都是可选的。

| 成员 | 签名 | 用途 |
|---|---|---|
| `name` | `str` | 插件标识，尽量和目录名一致 |
| `ctx` | `PluginContext` | 宿主注入的插件上下文 |
| `on_message` | `async (HookContext) -> None` | 旁听消息；不要在这里做慢操作 |
| `user_suffix` | `async (HookContext) -> str \| None` | 追加到当前 user 消息末尾；适合动态注入 |
| `system_prompt` | `async (HookContext) -> str \| None` | 追加到 system；**只放固定内容**，不要放时间/群名 |
| `after_reply` | `async (HookContext, str) -> str` | 改写即将发送的文本 |
| `settings_fields` | `async (PluginContext, fields) -> list \| None` | 动态改写设置面板字段 |
| `tools` | `() -> list[Tool]` | 模型工具；默认读类属性 `TOOLS` |
| `on_start` / `on_stop` | `()` 或 async | 生命周期；`on_stop` 要能取消后台任务 |

### 1.2 `PluginContext`

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | `str` | 插件 id |
| `title` | `str` | 显示名 |
| `settings` | `dict` | 当前设置（来自 `plugin.toml` + `data/plugin_data/<id>/settings.toml`） |
| `services` | `dict` | `llm` / `store` / `service` / `cfg` 等宿主服务 |
| `data_dir` | `Path \| None` | `data/plugin_data/<id>/` |
| `panel_url` | `str \| None` | 清单里声明的面板地址 |
| `log` | LoggerAdapter | 自动带 `[显示名]` 前缀 |
| `get(key, default)` | — | 取设置 |
| `spawn(coro)` | `Task` | 起受管后台任务，进程关闭时统一取消 |

### 1.3 `HookContext`

| 字段 | 说明 |
|---|---|
| `event` | `MessageEvent`：`self_id`、`message_type`、`message_id`、`user_id`、`group_id`、`content`、`sender_name`、`group_name`、`is_group`、`session_key` |
| `session_key` | `group:<群号>` / `private:<QQ号>` |
| `is_group` | 仅元数据；工具不按它分流 |
| `history` | 当前这轮送进模型的历史（只读；不要改） |

### 1.4 `Tool` / `ToolContext`

```python
Tool(
    name="demo_echo",
    description="回显文本",
    parameters={"type": "object", "properties": {"text": {"type": "string"}}},
    handler=echo,
    read_only=True,
)
```

| 字段 | 说明 |
|---|---|
| `name` | 全局唯一，≤64 字符，只允许字母数字下划线 |
| `description` | 给模型看的用途说明 |
| `parameters` | JSON Schema |
| `handler` | `async (ToolContext, dict) -> Any`，返回值必须可 JSON 序列化 |
| `read_only` | 只读标记；写工具由插件自己控制 |
| `schema()` | 生成 OpenAI `tools` 里的 function schema |

`ToolContext`：`plugin`、`session_key`、`is_group`、`is_admin`、`trigger`、`data_dir`、`log`。

运行约束由宿主 `ToolRunner` 统一处理：最多 3 轮、单工具 10 秒、结果 8KB 截断、异常回
`{"ok": false, "error": ...}`，工具消息不落持久历史。

### 1.5 `Command` / `CommandContext`

```python
Command(
    name="demo",
    handler=handle,
    help="示例命令",
    admin=False,
    aliases=["d"],
)
```

`CommandContext`：`plugin`、`name`、`args`、`text`、`event`、`session_key`、`is_group`、`is_admin`、`sender_name`。
命令由用户输入 `/name` 触发，不进模型、不进历史。

## 2. 插件清单（`plugin.toml`）

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 建议 | 必须和目录名一致 |
| `title` | 是 | 面板显示名 |
| `description` | 否 | 卡片说明 |
| `version` | 是 | `x.y.z` |
| `api` | 否 | 契约版本，默认 1；新插件写 2 |
| `entry` | 否 | 入口文件，默认 `plugin.py` |
| `panel` | 否 | 面板地址，如 `/plugins/demo` |
| `app_version` | 否 | 要求的 qq-bot-lite 版本范围，只警告不拦截 |
| `[[config]]` | 否 | 设置字段：`key`、`label`、`type`、`default`、`min`/`max`、`options`、`group`、`help` |

`setup(ctx)` 必须存在；返回 `Extension` 实例（或 awaitable）。

## 3. 宿主 HTTP API

所有接口默认只在本机面板使用；项目没有面向公网的鉴权层。

### 3.1 状态与页面

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 设置页 |
| GET | `/healthz` | 健康检查 |
| GET | `/api/status` | 在线状态、收发计数、首字统计 |
| POST | `/api/shutdown` | 优雅停机；只接受本机请求 |
| GET | `/manual` | 本地文档首页 |
| GET | `/manual/{path}` | 本地 Markdown 文档；只允许 `docs/` 和 `plugins/*.md` |
| GET | `/static/{name}` | 静态资源，带 mtime 版本参数 |

### 3.2 配置

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/config` | schema + 当前值 + 配置文件路径 |
| POST | `/api/config` | `{"updates": {...}}`；校验后逐行写 TOML |

### 3.3 模型与人设

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/models` | 模型 profile 列表 + 当前 |
| POST | `/api/models` | 新建/切换/排序/删除/保存 profile |
| GET | `/api/llm/check` | 探测 LM Studio 与已加载模型 |
| GET | `/api/personas` | 人设列表 + 当前 |
| POST | `/api/personas/create` | 新建人设 |
| POST | `/api/personas/active` | 切换当前人设 |
| DELETE | `/api/personas/{name}` | 删除人设 |
| POST | `/api/personas/order` | 保存人设顺序 |
| GET/POST | `/api/persona` | 读/写当前人设正文 |

### 3.4 插件

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/plugins` | 插件列表（含 `enabled`、`installed_at`、`last_error`） |
| POST | `/api/plugins/install` | 上传 ZIP；默认启用 |
| GET | `/api/plugins/{name}/readme` | 读 README，返回清洗后的 HTML |
| POST | `/api/plugins/{name}/enabled` | `{"enabled": true/false}` |
| POST | `/api/plugins/{name}/reload` | 重载单个插件 |
| DELETE | `/api/plugins/{name}?data=0\|1` | 删除插件，可选删数据 |
| GET/POST | `/api/plugins/{name}/settings` | 读/写插件设置 |

插件自己的 `router` 会挂在 `/plugins/<id>` 下；`pages/index.html` 会挂在
`/plugins/<id>/`。这部分由插件定义，不在主 API 表里展开。

## 4. 最小示例

```python
from app.sdk import Extension, Tool, ToolContext


async def echo(ctx: ToolContext, args: dict):
    return {"echo": args.get("text", "")}


class Demo(Extension):
    name = "demo"

    TOOLS = [
        Tool(
            name="demo_echo",
            description="回显文本",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=echo,
        )
    ]


def setup(ctx):
    return Demo()
```

对应 `plugin.toml`：

```toml
id = "demo"
title = "演示"
version = "0.1.0"
api = 2
```

## 5. 相关文档

- [插件规范](plugin.md)：完整契约、面板、设置、安装细节
- [开发文档](development.md)：主链路、上下文、测试、发布
- [使用指南](guide.md)：部署和设置
- [设置说明](settings.md)：每个 WebUI 设置项
