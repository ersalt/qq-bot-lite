# qq-bot-lite

量身定制的 QQ 机器人：OneBot 11 反向 WS 接入 SnowLuma，单次 LLM 请求打 LM Studio。

从 AstrBot 和 qq-maid-bot 的踩坑里长出来的最小实现，只保留确实要用的东西：

- 群聊 + 私聊
- 人设（外部 Markdown）
- 最近 100 条上下文，条数 + token 双重预算
- 引用回复、真正 @、长回复分段发送、重复内容去重
- 图片识别（下载 → 缩放 → base64 送模型）
- 单次模型请求，不走 Agent tool loop
- 一份说人话的 `config/config.toml`
- 插件式扩展点（钩子、命令、面板、设置），规范见 [docs/plugin-spec.md](docs/plugin-spec.md)

> **定位**：AstrBot 为云端模型设计，本地部署是它的兼容项；本项目反着来——KV 缓存、
> 显存余量、并发槽位这些"云上看不见"的东西才是一等公民。取舍与实测账本见
> [docs/local-first.md](docs/local-first.md)。

## 链路

```text
QQ ↔ SnowLuma ↔ qq-bot-lite(8790) ↔ LM Studio(1234)
```

qq-bot-lite 自己就是 OneBot 11 **反向 WS 服务端**，SnowLuma 作为客户端连进来，
收发都在同一条 WS 上完成（和 qq-maid-bot 的做法一致）。

## 快速开始

双击 `启动.cmd`（`start.cmd` 是同一个东西）。它会开一个控制台窗口，首次运行自动建
`.venv` 并装依赖（Python 3.12+，本机是 3.13.5）。

启动后：

- 状态页：<http://127.0.0.1:8790/>
- 健康检查：<http://127.0.0.1:8790/healthz>
- OneBot 入口：`ws://127.0.0.1:8790/onebot/v11/ws`

关窗口或 Ctrl+C 停止。正常退出直接关窗，异常退出会停住让你看报错。

### 黑窗里能看到什么

```text
──────────────────────────────────────────────────────────
  qq-bot-lite v0.1.0  pid 22136
  反向 WS   ws://127.0.0.1:8790/onebot/v11/ws
  模型      qwen3.8-27b @ http://127.0.0.1:1234/v1  (stream=true)
  ...
──────────────────────────────────────────────────────────

19:43:08  OneBot 已连接: Address(host='127.0.0.1', port=50917)
19:43:27  ← 群「测试群」(1058912272) | 体验极差(1140853441) | msg=183746 | 我是你的爸爸
19:43:34  → 群「测试群」(1058912272) | 回复 体验极差(1140853441) | 1 段 | 引用=183746 | @=1140853441 | 首字 6636ms | 总计 7.9s | tokens 1566/249（思考 216，正文 33）
```

- `群「群名」(群号)`：群名来自 `get_group_info`，查不到显示「未知群名」，缓存 10 分钟。
- `昵称(QQ号)`：优先群名片，其次昵称。
- `msg=` 是 OneBot 的 `message_id`，出站日志里的 `引用=` 就是用户那条消息的 id。
- `←` 收到的、需要回复的消息；`→` 发出的回复、实际引用/@ 了谁、几段、延迟和 token 消耗。
- `tokens 输入/输出（思考 X，正文 Y）`：思考也算进输出配额，正文为 0 说明 `max_tokens` 不够。
- 默认只显示被点名的消息，群里的闲聊不刷屏。想看全部用 `--log-level debug`。
- 窗口标题常驻 `在线/离线 + 收/回条数`，不滚日志也知道它活着。

### 接 SnowLuma

SnowLuma 的 OneBot 配置在 `D:\ProgramFiles\SnowLuma\config\onebot_<QQ号>.json`，
把 `wsClients[0].url` 从 8789 改成 8790，并把 `accessToken` 改成和
`config.toml` 里 `[onebot] access_token` 一致：

```json
{
  "name": "wsclient-1",
  "accessToken": "maid-local-2026",
  "messageFormat": "array",
  "url": "ws://127.0.0.1:8790/onebot/v11/ws",
  "role": "Universal",
  "reconnectIntervalMs": 5000
}
```

改完重启 SnowLuma（或在其界面里改）。连上后状态页会显示「在线」和 self_id。

> 同一时间只让一个机器人占用 SnowLuma 的 OneBot 连接，否则会重复收消息、重复回复。

### 检查模型

```powershell
.\.venv\Scripts\python.exe scripts\check_llm.py
```

会列出 LM Studio 里已加载的模型，确认 `[llm] model` 写的是哪个 id，并跑一次最小请求。

## 配置

全部在 `config/config.toml`，带注释。几个关键项：

| 配置 | 作用 |
|---|---|
| `[onebot] access_token` | 必须和 SnowLuma 侧一致 |
| `[llm] stream` | 流式，决定首条回复能多快发出去 |
| `[llm] max_tokens` | 思考 + 回答共用的总配额，同时是质量闸和延迟闸 |
| `[llm] extra_body` | 原样塞进请求体，默认空（思考开着）；`{ reasoning_effort = "low" }` 可略微收敛 |
| `[context] max_messages` / `max_tokens` | 上下文条数与预算，超了从最旧丢 |
| `[trigger]` | 群里 @ / 前缀 / 关键词 / 引用机器人的消息 都能触发；私聊全回 |
| `[reply] quote/at_sender/segments` | 引用、真 @、分段三件套 |
| `[images]` | 开关、张数、字节上限、缩放边长、是否走系统代理 |
| `[extensions] enabled` | 要挂载的扩展模块名 |

## 为什么不会再有 “System message must be at the beginning”

Qwen 系模板要求 system 只能在开头。设计上就只发一条 system，历史里全是
user/assistant，动态内容（时间、场景、群语汇）拼进那条 system。
所以不需要 qq-maid-bot 当初那个 1235 兼容代理。

## 延迟

- 单次模型请求，没有多轮 tool loop。
- 流式返回，凑够一句就发，不等整段生成完。
- 同会话串行（避免两条回复互相盖），不同会话并行。

`logs/app.log` 里每条回复都会打 `首字 xxx ms | 总计 x.xs | tokens 输入/输出（思考 X，正文 Y）`。

## 扩展（群语汇第二阶段）

`app/extensions/` 下放一个模块，实现其中任意钩子，然后在 `config.toml` 里启用：

```python
# app/extensions/slang.py
class Extension:
    name = "slang"

    async def on_message(self, ctx):
        """旁听所有消息，用于学习和统计。"""

    async def system_prompt(self, ctx):
        """返回一段文本，会被拼进 system 末尾。"""
        return "群里的黑话：……"

    async def after_reply(self, ctx, text):
        """改写即将发出的文本。"""
        return text
```

```toml
[extensions]
enabled = ["slang"]
```

扩展抛异常只记日志，不影响聊天主流程。钩子上下文 `ctx` 带 `event`、`session_key`、
`is_group`、`history`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

用假 SnowLuma + 假 LM Studio 跑通「鉴权 → 事件 → 触发 → 上下文 → 流式 → 引用/@/分段」，
不需要开 QQ，也不需要开模型。

## 项目规范

| 项 | 约定 |
|---|---|
| 分支 | `main`（只收合并，不直接提交）/ `develop`（日常开发）/ `feature/*`、`fix/*`、`hotfix/*` |
| 版本来源 | 根目录 `VERSION`，代码通过 `app.__version__` 读取，不重复写死 |
| 日志 | `CHANGELOG.md` 以上一个正式 tag 为基线，只写用户可感知的变化 |
| 测试 | `python -m pytest tests -q`（CI 跑 3.12/3.13 × Ubuntu/Windows） |
| 发布 | develop 合并到 main（`--no-ff`）→ 打附注 tag `vX.Y.Z` → GitHub Release |

发布流程：

```powershell
# 1. develop 上改版本号、补 CHANGELOG、跑测试
# 2. 合并并打 tag
git switch main
git merge --no-ff develop -m "release: vX.Y.Z"
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main --tags
```

## 目录

```text
app/
  main.py           FastAPI 装配 + 状态页
  __main__.py       启动入口
  config.py         TOML 加载
  persona.py        人设加载（改文件即时生效）
  onebot/           反向 WS 服务端、事件解析、action 调用
  core/             触发判断、主流程
  llm/              OpenAI 兼容客户端、提示词、token 估算
  context/          SQLite 存储、上下文裁剪
  reply/            分段器、发送器（引用/@/去重）
  media/            图片下载与缩放
  pluginkit/        插件运行时：清单、加载、钩子、设置、自检
config/config.toml  唯一配置源
personas/           人设 Markdown
plugins/            插件（目录名即插件 id；_template 是骨架，不会被加载）
data/app.db         会话数据库
data/plugin_data/   插件运行期数据
logs/app.log        运行日志
docs/               设计文档、插件规范、实测账本
tests/              端到端自测
VERSION             版本号唯一来源
CHANGELOG.md        更新日志
启动.cmd            双击启动（开黑窗）
```
