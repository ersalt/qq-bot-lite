# 插件

`app/extensions/` 下的每个 `.py` 就是一个插件，文件名就是插件名。放进去之后
设置面板的「插件」页会自动出现一张卡，不用注册、不用改主配置的代码。

## 最小插件

```python
# app/extensions/ping.py
MANIFEST = {
    "title": "回音",
    "description": "被叫到的时候顺带回一句。",
    "version": "0.1.0",
    "author": "你",
}

class Ping(Extension):
    name = "ping"

    async def system_prompt(self, ctx):
        return None

def setup(ctx):
    return Ping()
```

## 会被发现的约定

| 名字 | 必需 | 作用 |
|---|---|---|
| `MANIFEST` | 是 | 元数据；缺了这张卡会显示成红色错误卡，而不是消失 |
| `setup(ctx)` | 否 | 返回扩展实例；也可以用 `class Extension` 子类代替 |
| `router` | 否 | 一个 `APIRouter`，挂到 `/plugins/<名字>`，面板上的「打开面板」就跳到这 |

`MANIFEST` 支持的键：

```python
{
  "title": "运行统计",          # 卡片标题，缺省用文件名
  "description": "...",        # 卡片说明，最多显示三行
  "version": "0.1.0",
  "author": "内建",
  "panel": "/plugins/stats",   # 有值才显示「打开面板」
  "config": [                  # 有值才显示「设置」
      {"key": "window_hours", "label": "统计窗口（小时）", "type": "number",
       "min": 1, "max": 720, "default": 24, "help": "说明文字"},
      {"key": "show_latency", "label": "显示延迟", "type": "bool", "default": True},
  ],
}
```

`config` 里每个字段的 `key` 会原样进 `config/plugins.toml` 的 `[插件名]` 段，
插件里用 `ctx.settings.get(key)` 读。

## 钩子

继承 `Extension` 并实现任意一个：

| 钩子 | 时机 | 用途 |
|---|---|---|
| `on_message(ctx)` | 每条消息判定完触发之后、回复之前 | 计数、学习、记录 |
| `system_prompt(ctx)` | 拼 system 提示词时 | 返回一段文本，追加到 system 末尾 |
| `after_reply(ctx, text)` | 文本要发出去之前 | 改写、过滤 |

`ctx` 是 `HookContext`，带 `event`（消息事件）、`session_key`、`is_group`、`history`。

**约定：钩子抛异常只记日志，绝不影响聊天主流程。** 这条在注册表里强制了。

## 插件拿到什么

`setup(ctx)` 的 `ctx` 是 `PluginContext`：

- `ctx.name`：插件名
- `ctx.settings`：`config/plugins.toml` 里这一段合并默认值后的字典
- `ctx.services`：`{"store": 消息库, "service": ChatService, "cfg": 配置}`

给得很少是故意的。插件想要更多能力，就说明该把它做成核心功能。

## 例：内建的 stats

`app/extensions/stats.py` 是一个完整例子：MANIFEST 里带两个设置项，`router`
提供了 `/data` 和一个小面板页，卡片上「设置」和「打开面板」两个按钮都能用。

## 禁用

面板上关掉开关即可，等价于把名字从 `config/config.toml` 的
`[extensions] enabled` 里删掉。禁用后它的面板路由也会一起撤掉。
