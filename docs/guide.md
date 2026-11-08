# qq-bot-lite 使用指南

> 这份文档面向**部署和使用机器人的人**。每个设置项的解释见[设置说明](settings.md)；
> 开发、插件契约、内部链路见[开发文档](development.md)。
>
> 所有文档都在项目仓库里，不需要外部服务器；启动机器人后也可以从设置页签栏的「文档」阅读。

## 1. 这是什么

qq-bot-lite 是一个本地优先的 QQ 机器人：

```text
QQ ↔ SnowLuma ↔ qq-bot-lite（8790） ↔ LM Studio（1234）
```

- **SnowLuma** 负责 QQ 侧，qq-bot-lite 自己充当 OneBot 11 反向 WS 服务端。
- **LM Studio** 跑本地模型，qq-bot-lite 通过 OpenAI 兼容接口调用。
- 会话、插件数据、图片都在本机，默认不依赖云端服务。
- 适合自己用、小群用；不适合多租户、大规模并发或需要公网托管的场景。

两个内置插件默认开启：

| 插件 | 作用 |
|---|---|
| 鲸语汇 `whale_lexicon` | 从群聊里学习黑话，命中时注入提示；带独立面板 |
| 鲸鱼空间 `bot_space` | bot 自己写的 Markdown 手记，模型用工具读写，用户只读查看 |

## 2. 准备

- Windows 10/11（当前主要开发和验证环境）。
- Python 3.12+；项目自带 `.venv` 启动脚本会自动创建。
- SnowLuma，或兼容 OneBot 11 的 QQ 端。
- LM Studio，已下载一个能用的模型（当前默认 `qwen3.8-27b`）。
- 本机端口：`8790`（qq-bot-lite）、`1234`（LM Studio，可改）。

> 不需要公网 IP，也不需要开服务器。QQ、SnowLuma、LM Studio 和本项目的通信都在本机完成。

## 3. 第一次启动

1. 把项目放到一个固定目录，例如 `D:\Dev\qq-bot-lite`。
2. 双击 `启动.cmd`（`start.cmd` 是同一入口）。
3. 首次启动会做这些事：
   - 创建 `.venv` 并安装 `requirements.txt`；
   - 从 `config/config.example.toml` 复制出 `config/config.toml`；
   - 启动 FastAPI，监听 `127.0.0.1:8790`。
4. 浏览器打开 <http://127.0.0.1:8790/>。

停止：关控制台窗口，或按 `Ctrl+C`。需要远程/脚本停服时：

```powershell
Invoke-WebRequest -Method POST -Uri http://127.0.0.1:8790/api/shutdown
```

### 目录速查

```text
config/config.toml      本机配置，不入库
data/app.db             会话数据库
data/plugin_data/       插件数据（鲸语汇词库、鲸鱼空间手记）
data/images/            历史图片本地副本
logs/                   每次启动一个日志，latest.txt 指向最新
personas/               人设 Markdown
plugins/                内置插件源码、测试、文档
```

## 4. 接 QQ：SnowLuma

在 SnowLuma 的 OneBot 配置里，把反向 WS 指向：

```text
ws://127.0.0.1:8790/onebot/v11/ws
```

关键项：

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

- `accessToken` 必须和 `config/config.toml` 的 `[onebot] access_token` 一致。
- `messageFormat` 用 `array`；项目按 OneBot 11 消息段解析。
- 同一时间只让一个 QQ 端连这个入口；多开会重复收消息、重复回复。
- 连上后 WebUI 顶栏会显示「已连接」和机器人 QQ 号，控制台也会出现 `OneBot 已连接`。

## 5. 接模型：LM Studio

1. 在 LM Studio 加载模型。
2. 打开 Local Server（OpenAI Compatible），默认地址 `http://127.0.0.1:1234/v1`。
3. 确认 `config/config.toml`：

```toml
[llm]
base_url = "http://127.0.0.1:1234/v1"
api_key = "lm-studio"
model = "qwen3.8-27b"
```

`model` 必须和 LM Studio 里显示的模型标识一致。检查：

```powershell
.\.venv\Scripts\python.exe scripts\check_llm.py
```

它会列出可用模型并发一次最小请求。

### 模型相关设置怎么理解

| 设置 | 建议 |
|---|---|
| `stream` | 默认 `false`。一条回复本来就只有几秒，非流式更省心；打开后首字更早，但工具调用仍走非流式 |
| `max_tokens` | `0` = 不发送，让模型自己收尾；设太小会把思考吃光，正文变空 |
| `reasoning_effort` | 留空用模型模板默认；`none` 更快但活人感会下降 |
| `max_concurrency` | 全局资源设置，不随模型配置切换。默认 1；多个群同时聊会互相抢显存和缓存，单群体验最好 |
| `max_context` | 软件侧硬上限；`0` = 不额外限制，靠 `[context] max_tokens` 控制历史 |

## 6. WebUI

设置页在 <http://127.0.0.1:8790/>。

- 改动先留在页面上，点「保存」才写回 `config/config.toml`；「放弃改动」丢弃未保存内容。
- 需要重启的设置会标「需重启」；保存后顶部会出现提示。
- 主题按钮切换明暗；全屏按钮只影响当前页面。
- 设置页签栏的「文档」打开本地文档；不再提供独立文档页。

### 设置分组

| 分组 | 内容 |
|---|---|
| 服务与访问 | 监听地址、端口、日志级别、状态行间隔 |
| OneBot 连接 | WS 路径、access token、动作超时、最大连接数 |
| 模型 | LM Studio 地址、模型、采样、流式、思考、并发 |
| 上下文 | 历史条数、token 预算、是否只留问答、是否带时间戳 |
| 人设 | 人设文件、机器人昵称、人设顺序 |
| 触发规则 | 私聊全回、群聊 @/前缀/关键词/冷却 |
| 回复方式 | 分段、引用、@、提及解析、去重 |
| 图片 | 识图开关、张数、单张上限、最长边、历史图片、代理 |
| 插件 | 安装、启停、设置、文档、重载、删除、搜索/排序/分页 |
| 命令 | `/xxx` 前缀、管理员、超时 |

### 上下文条数不是硬上限

`[context] max_messages` 是「接过的问答消息」的软上限：

- 只统计 bot 接过的轮次（一问一答算 2 条），群里跟它无关的闲话不进持久历史；
- 40 条约等于 20 轮；锚点窗口会在 100%~125% 之间浮动，超过 125% 才回落到上限；
- 还会被 `[context] max_tokens` 二次裁剪；图片和群聊 ambient 另行占预算。

所以日志里偶尔看到 46 条不是 bug。要它记长期的事，优先用鲸鱼空间的手记，不要把窗口无脑拉大。

### 图片大小

面板里的「单张大小上限（KB）」按 KB 填，例如 `8192` 表示 8MB；配置文件里仍按字节保存为 `8388608`。这是为了面板不出现一长串 0。

## 7. 内置插件

两个插件都在 `plugins/` 下，和主仓库同测试、同发版；默认启用。插件页的「文档」按钮会打开插件自己的 README。

### 7.1 鲸语汇（whale_lexicon）

群黑话自学习：

1. 旁听群消息，攒够一批；
2. 等机器人闲下来，分批调本地模型提取；
3. 模型自审，确定的直接入库，拿不准的进待审；
4. 命中时把释义注入当前提示词；没命中时用梗百科兜底。

常用设置：

| 设置 | 默认 | 说明 |
|---|---|---|
| 总开关 | 开 | 关掉后不学也不注入，数据保留 |
| 也学习和注入私聊 | 关 | 私聊默认不参与 |
| 自动学习 | 开 | 关掉后不再攒消息，面板的「学一批」也没东西可学 |
| 每群攒多少条触发 | 50 | 达到后排队 |
| 空闲多少秒后才学 | 300 | 避免和聊天抢模型；0 = 不等 |
| 单次最多注入 | 3 | 一条消息最多带几个词条 |

数据在 `data/plugin_data/whale_lexicon/jargon.db`。面板在「插件」→ 鲸语汇 → 打开面板。

完整说明见 [鲸语汇 README](../plugins/whale_lexicon/README.md)。

### 7.2 鲸鱼空间（bot_space）

bot 的私人 Markdown 手记，全局一份，不分群聊私聊：

- 模型用 `space_*` 工具自己决定读、写、改、搜；
- 用户只能只读查看，没有 `/space` 命令，也没有「让 bot 写一条」按钮；
- 写入有路径约束、配额、审计和软删除；不是 OS 沙箱。

常用设置：

| 设置 | 默认 | 说明 |
|---|---|---|
| 允许模型创建/追加 | 开 | 关掉后只剩 list/read/search |
| 空间自主权说明 | 开 | 在 system 里加一段固定说明；关掉省 token |
| 单文件上限 | 64KB | 追加/编辑超限会报错 |
| 文件总数上限 | 200 | 含回收站 |
| 手记总量上限 | 10MB | 含回收站 |
| 单次读取上限 | 6000 字 | 长文用 `offset` 续读 |

数据在 `data/plugin_data/bot_space/space/`。面板在「插件」→ 鲸鱼空间 → 打开面板。

完整说明见 [鲸鱼空间 README](../plugins/bot_space/README.md)；开发设计记录只看插件源码里的
`plugins/bot_space/docs/`，不放进普通用户文档。

### 7.3 插件管理

- **安装 ZIP**：安装成功后默认启用；加载失败会在卡片上显示原因。
- **未启用时点「打开面板」**：不会跳空白页，会提示先打开卡片右上角的开关。
- **搜索 / 排序 / 分页**：按名称、ID、说明搜索；可按名称、安装时间、启用优先排序；每页 6 张。
- **删除**：可选只删插件或连 `data/plugin_data/<id>/` 一起删；删除不可撤销。
- **重载**：重跑 `setup()`；router 增删仍需要重启进程。

## 8. 日常使用

### 触发

- 私聊：默认每条都回（`private_always = true`）。
- 群聊：默认被 @ 才回；也可以配前缀、关键词、引用机器人。
- 冷却：`cooldown` 控制同一会话两次回复的最小间隔。

### 回复

- 引用触发消息、真 @、长回复分段、重复内容去重都可以单独开关。
- 模型写「at 昵称」时，会在群成员里查 QQ 号并换成真正的 at。
- 分段默认只在长回复或句末标点处切；短回复就是一条。

### 日志

`logs/app-YYYYMMDD-HHMMSS.log`，`logs/latest.txt` 指向最新。每条回复会记：

```text
首字=xxxms 总计=x.xs tokens in=... out=...（思考 X，正文 Y）
```

- `首字` 是模型第一个 token 的时间，不是发送时间；
- `正文` 为 0 通常说明思考把配额吃光了；
- 上下文那行会显示历史条数、估算 token、ambient 行数和图片数。

## 9. 数据、备份与升级

- `data/app.db`：会话与消息；`data/plugin_data/`：插件数据；`data/images/`：图片副本。
- `config/config.toml`：本机配置，不入库；升级不会覆盖。
- 备份：停服后复制 `data/`、`config/`、`personas/` 即可。
- 升级：拉取代码 → 重新启动（依赖变化时启动脚本会装）；插件源码已随主仓库更新。
- 删除插件数据后不可恢复；先确认不需要再删。

## 10. 常见问题

**SnowLuma 一直连不上？**

- 确认 URL 是 `ws://127.0.0.1:8790/onebot/v11/ws`；
- 确认 access token 两边一致；
- 看 8790 是否被别的进程占用；
- 看控制台有没有 `OneBot 已连接`。

**模型报错 / 不回？**

- 先跑 `scripts/check_llm.py`；
- 确认 LM Studio 的 Local Server 开着，`model` 名称一致；
- 看日志里的 `finish_reason` 和 `正文` token；思考吃光配额时把 `max_tokens` 调大或减少思考。

**为什么它记不住刚才的事？**

- 默认只留接过的问答轮次，群聊闲话不进持久历史；
- 40 条是软上限，还会被 token 预算裁剪；
- 长期记忆让它写进鲸鱼空间，而不是指望上下文窗口。

**插件装完没反应？**

- 新装插件默认启用；卡片上出现 `last_error` 说明加载失败；
- 点「重载」重试；router 变更需要重启进程。

**图片不识别？**

- 确认 `[images] enabled = true`；
- 单张大小上限单位是 KB；网络代理按需开 `trust_env`；
- 截图类图片把 `max_side` 调到 768~1024 更清楚。

**能不用公网服务器吗？**

可以。QQ、SnowLuma、LM Studio、本项目全在本机；文档也在仓库和本地面板里。

---

需要改代码或写插件？继续看[开发文档](development.md)。
