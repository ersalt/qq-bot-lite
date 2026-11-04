# bot_space 设计交接（未实现）

> 状态：设计稿，供 `D:\Dev\bot_space` 新工作区接手。
> 主项目：`D:\Dev\qq-bot-lite`。
> 2026-11-04：Phase 1 核心工具协议已在主项目落地（`complete_message` / `Tool` /
> `ToolRunner` / `api=2`）；插件本身尚未实现。
> 这页先把「为什么做、做成什么样、哪些不能做」写清楚；实现时以这里和
> `docs/plugin.md` 的接口契约为准，冲突先改文档再改代码。

---

## 0. 一句话

`bot_space` 是 bot 的私人 Markdown 空间。插件**只向模型提供工具，不提供用户命令**；
模型自己决定要不要读写，用户不通过 `/命令` 强行操控。主项目只增加通用的「工具协议 +
工具循环」，具体工具全部留在插件里。

---

## 1. 前因

### 1.1 项目边界

qq-bot-lite 的定位是本地轻量 QQ 机器人：OneBot 11 + FastAPI + SQLite + 本机 LM Studio。
主项目只保留内核能力（消息、上下文、模型、回复、插件宿主、设置面板），功能尽量插件化。
插件源码在独立仓库，部署副本放 `plugins/<id>/`；主仓库 `.gitignore` 已忽略部署副本。

### 1.2 讨论结论

- **不做沙箱。** 不搞「先给高权限、再限制到小空间」。插件跑在 bot 进程里，Python 本身
  不受目录限制；目录限制只能约束模型通过工具能碰到什么，不是 OS 级隔离。
- **用能力表。** 插件注册一组工具，模型只能调用表里有的；删掉工具就删掉权限。
- **固定根目录。** 工具只能写 `ctx.data_dir / "space"` 里面的 `.md`，防止误伤项目其他文件。
- **不做用户命令。** 不打 `/space ...`。空间是 bot 的私人空间，是否记录由模型决定；
  用户最多在插件面板只读查看，不提供「让它写」的按钮。
- **存储放插件数据。** `data/plugin_data/bot_space/`，默认关闭；删除插件时现有弹窗的
  「连数据一起删」就是生命周期开关。其他用户不喜欢可以直接删干净。
- **原生工具调用。** 不自己发明 slash command 协议；走 OpenAI 兼容的
  `tools` / `tool_calls` / `role=tool` 循环，和行业主流一致。
- **通用协议进 SDK，具体工具进插件。** 不要把 `space_list/read/append` 塞进主项目核心；
  主项目只提供 `Tool` / `ToolContext` / `ToolRunner`，保持轻量。

### 1.3 已做的验证

本机 LM Studio（`qwen3.8-27b`）支持原生 tool calls。用 OpenAI 兼容请求带一个
`space_append` 工具，返回：

```json
{
  "finish_reason": "tool_calls",
  "message": {
    "tool_calls": [{
      "type": "function",
      "function": {
        "name": "space_append",
        "arguments": "{\"path\":\"note.md\",\"text\":\"...\"}"
      }
    }]
  }
}
```

结论：模型/服务端这一层可行；**当前 `LLMClient.complete()` 只取 `content`，会丢掉
`tool_calls`**，所以必须先补核心协议，否则插件写了也接不住。

---

## 2. 目标 / 非目标

### 目标

- 模型可以在私聊里自己决定：查看空间、读某篇笔记、搜索、创建、追加。
- 工具权限是一张显式的表；移除表项就移除能力，未知工具直接拒绝。
- 固定根目录 + 路径清洗 + 配额 + 原子写 + 审计。
- 插件默认关闭；删除插件数据即清空空间，不污染主项目。
- 给其他插件开发者一份可复用的工具协议示例。

### 非目标

- 不做 OS 沙箱、容器、独立用户。
- 不做 shell / Python 执行 / 网络访问。
- 不做通用文件管理器（重命名、移动、任意删除、二进制、图片）。
- 不做用户命令触发。
- 不主动发 QQ 消息（v1）。
- 不接 MCP（进程内插件不需要）。
- 不让模型 delete / overwrite 已有文件。

---

## 3. 总体结构

```
qq-bot-lite（主项目）
  app/llm/client.py       complete_message()：保留 tool_calls
  app/sdk.py              Tool / ToolContext（新）
  app/pluginkit/          收集插件 TOOLS，做命名冲突检查
  app/core/tool_runner.py ToolRunner：执行 tool_calls、回填 role=tool
  app/core/pipeline.py    ChatService 调用 ToolRunner（私聊）
  docs/plugin.md          api=2 契约 + 示例

bot_space（独立插件仓库）
  plugin.toml             api = 2, panel, 设置
  plugin.py               setup(ctx), TOOLS, router
  core/space.py           路径/配额/原子写/审计（纯逻辑，不 import app.*）
  core/tools.py           工具 handler
  pages/index.html        只读面板
  tests/                  路径、配额、工具 schema
  tools/deploy-to-bot.ps1 部署到主项目 plugins/bot_space
```

原则：**主项目管协议和循环，插件管工具和存储。**

---

## 4. 核心工具协议（主项目要实现）

### 4.1 LLM 客户端

新增一个返回完整 message 的方法，保留现有 `complete()` 不变：

```python
async def complete_message(
    self,
    messages: list[dict],
    tools: list[dict] | None = None,
    override: dict | None = None,
    timeout: float | None = None,
) -> dict:
    """返回 choices[0].message，包含 content / tool_calls / reasoning_content。"""
```

`complete()` 可以内部调用它并只取 `content`，避免两套请求逻辑。

请求体按 OpenAI 兼容格式：

```json
{
  "model": "...",
  "messages": [...],
  "tools": [{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}],
  "tool_choice": "auto",
  "stream": false
}
```

注意：

- 工具循环第一期**只支持非流式**。`[llm] stream = true` 时要么不暴露工具，要么后续再补
  流式 tool_calls；不要假装支持。
- 工具 schema 每轮请求都带；工具表保持稳定，避免无谓的 KV 缓存变化。
- 工具列表为空时，请求和不带工具的普通聊天完全一致。

### 4.2 SDK 类型

`app/sdk.py` 增加（名字可调整，但语义保持）：

```python
@dataclass(slots=True)
class ToolContext:
    plugin: str
    session_key: str
    is_group: bool
    is_admin: bool
    data_dir: Path | None
    log: Any
    trigger: str  # "chat" | "autonomy"

@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable[[ToolContext, dict], Awaitable[Any]]
    read_only: bool = True
```

插件导出：

```python
TOOLS = [
    Tool(
        name="space_append",
        description="向 bot 的私人空间追加 Markdown；只在确实需要记录时使用。",
        parameters={...},
        handler=space_append,
    ),
]
```

- 宿主检查工具名全局唯一；冲突在加载时报错，不静默覆盖。
- 工具名建议带插件前缀（`space_`），避免和别的插件撞。
- 不导出 `TOOLS` 就是普通钩子插件；`api=1` 老插件继续可用。
- 新插件写 `api = 2`；宿主支持 `api <= 2`。

### 4.3 ToolRunner

新增 `app/core/tool_runner.py`，职责：

1. 接收初始 messages + schema 列表。
2. 调用 `complete_message`。
3. 如果 message 没有 `tool_calls`，返回 `content`。
4. 如果有：
   - 把 assistant message（含 `tool_calls`）追加到工作 messages；
   - 逐个执行工具（顺序执行，文件操作不要并发）；
   - 校验工具名在允许表里；未知工具返回错误结果；
   - 结果序列化成字符串，追加 `{"role": "tool", "tool_call_id": ..., "content": ...}`；
   - 回到第 2 步，最多 3~4 轮。
5. 超过轮数/总超时：停止，记日志，返回一句保底文本或最后一个 `content`。
6. 工具异常、参数不是 dict、结果过大：都转成 `ok: false` 的 tool 结果，不抛出到聊天主链路。
7. 工具结果单条上限约 8KB；超了截断并标记。
8. 记录审计日志：工具名、耗时、成功/失败、路径、字符数；不记录完整隐私内容（或只记录摘要）。

**关键约束**

- ToolRunner 必须在 `ChatService` 现有的 `_llm_slot` 内运行，继续保持同一时间一条模型请求。
- 工具对话**不写持久历史**：下一次用户消息的历史里只有最终 user/assistant 问答，没有
  tool_calls/tool 结果。这样不会污染上下文，也不会让模型下次照抄工具 JSON。
- 工具循环里的 assistant/tool 消息只存在于本次请求。
- 最终回复继续走现有的 `after_reply`、分段发送、历史落库。

### 4.4 工具暴露策略

- 默认**只在私聊**暴露 `bot_space` 工具；群聊不暴露，避免别人诱导 bot 写东西。
- 插件可以设置 `allow_write` 等开关；宿主只把启用的工具放进 schema。
- 是否调用由模型决定；用户不打命令，最多自然语言说「记一下」，模型自己判断。
- 系统提示词保持逐字稳定；不要为了工具在 system 里拼动态内容。工具描述本身已经能表达用途。
- 如果模型不调用工具，不追问、不自动补写；这是它的决定。

---

## 5. bot_space 插件设计

### 5.1 清单

```toml
api = 2
title = "bot_space"
description = "bot 的私人 Markdown 空间：模型自己决定读写，用户只读查看。"
version = "0.1.0"
panel = "/plugins/bot_space"

[[config]]
key = "allow_write"
type = "bool"
default = true
label = "允许模型创建/追加"
help = "关掉后模型只剩 list/read/search，不再能写。"

[[config]]
key = "max_file_kb"
type = "number"
default = 64
...
```

不写 `COMMANDS`。插件启用后只有工具和只读面板。

### 5.2 工具集合（模型侧）

| 工具 | 作用 | 说明 |
|---|---|---|
| `space_list` | 列目录/文件 | 只返回名字、大小、修改时间、标题摘要 |
| `space_read` | 读一篇 `.md` | 结果截断到 8KB |
| `space_search` | 关键词搜索 | 最多返回 N 条，带路径和行号 |
| `space_create` | 新建 `.md` | 已存在则失败，不覆盖 |
| `space_append` | 追加 `.md` | 不存在则创建；这是模型最常用的写工具 |

**不给模型**：delete、overwrite、rename、move、任意扩展名、二进制、图片。
删除/清空只通过「删插件数据」这个宿主生命周期操作完成；面板也不提供编辑/触发。

### 5.3 目录

```
data/plugin_data/bot_space/
  space/
    notes/                模型自己的笔记
    journal/              可选：自主日记
    .audit.jsonl          工具审计
  settings.toml           插件设置（宿主管理）
```

- 根目录固定为 `ctx.data_dir / "space"`，不接受配置任意绝对路径。
- 模型只能传相对路径；允许一层或两层子目录，禁止 `..`、绝对路径、盘符、UNC、`:`。
- 只允许 `.md`；文件名 slug 化；Windows 保留名（CON/NUL/COM1…）、结尾空格/点拒绝。
- `Path.resolve()` 后 `relative_to(root)`；任一路径段是符号链接/junction 就拒绝。
- 原子写：同目录临时文件 + `os.replace`；同一路径用 `asyncio.Lock` 串行。
- 单文件 64KB（可配）、总文件 200、总量 10MB；超出返回明确错误给模型。
- 读结果、搜索结果都设上限，避免把上下文撑爆。

### 5.4 只读面板

- `pages/index.html`：左侧文件树，右侧 Markdown 原文/预览，顶部显示用量和审计入口。
- 只读：没有编辑框、没有新建/删除按钮、没有「让 bot 写」的触发。
- 可以显示「这是 bot 的私人空间；要清空请到插件卡片删除数据」。
- 面板接口全部 GET；不要提供 POST 写接口，减少误用面。
- 主题继续跟主面板 `data-theme` / `.dark` 走；组件用 Basecoat；下拉如果要用，用主面板的 `uiSelect`（参考 `docs/ui-spec.md`）。

### 5.5 审计

`.audit.jsonl` 每行：

```json
{"ts":"2026-11-04T23:00:00+08:00","tool":"space_append","path":"notes/x.md","chars":120,"ok":true,"session":"private:123"}
```

不记录完整正文；面板可看最近 N 条。日志同时走 `ctx.log`。

---

## 6. 权限模型

- **工具表就是权限表。** 删掉 `TOOLS` 里的某项，模型 schema 里就没有它；调用未知工具直接拒绝。
- 配置开关是运行时权限：`allow_write=false` 时只暴露读工具。
- 固定根目录是防误伤，不是安全沙箱；不要对外宣称「沙箱」。
- 模型侧永远没有 delete / overwrite；删除只能由宿主删插件数据完成。
- 私聊/主人可用；群聊不暴露工具。
- 插件代码本身仍是受信代码；如果以后要装不可信插件，才需要 OS 级隔离，那是另一个架构。

---

## 7. L2：模型在聊天里自己调用

流程：

```
用户私聊消息
  → 现有 pipeline 拼 system/history/ambient/quoted
  → 如果 bot_space 启用且私聊：带 TOOLS schema
  → 模型返回 content 或 tool_calls
  → 有 tool_calls：ToolRunner 执行、回填 role=tool、再请求（最多 3~4 轮）
  → 最终 content 走现有 after_reply / 分段发送 / 落库
```

- 工具对话不落历史；只落最终 assistant。
- 工具结果不塞 system；只在本次 tool 消息里。
- 模型没有调用工具时，行为和现在完全一样。
- 单轮工具循环会多花模型请求；日志记录轮数和耗时，方便评估是否值得。
- 先只在私聊开；群聊保持现在的纯聊天。

---

## 8. L3：自主触发

不是另一套权限，还是同一张工具表，只是触发者从「聊天」变成「定时/空闲」：

- `on_start` 起 `ctx.spawn` 的任务；`on_stop` 由宿主取消。
- 默认关闭；每天最多一次；只在长时间没消息时跑。
- 工具子集更小：`space_list` / `space_read` / `space_append`，只写 `journal/`。
- 不主动发 QQ；结果写文件，用户在面板看。
- 需要给 `ChatService` 一个公开的 LLM gate / `run_agent` 入口；插件不能直接调
  `llm` 绕过 `_llm_slot`，否则会和聊天抢模型、破坏单并发和 KV 缓存。
- 预算：每天 token/写入上限；失败重试一次；连续失败自动停。
- 第一版只做「每日 journal」这种固定任务，不做开放式「想干什么就干什么」。

---

## 9. 实现顺序

### Phase 1：主项目核心工具协议（qq-bot-lite）

1. `LLMClient.complete_message()` + 单测（用 fake HTTP / mock）。
2. `Tool` / `ToolContext` 进 `app/sdk.py`。
3. `ExtensionRegistry` 收集 `TOOLS`，检查重名；只对启用的插件暴露。
4. `ToolRunner` + 单测（假工具、超轮数、工具异常、结果截断）。
5. `ChatService` 私聊接入；工具对话不落历史；单测。
6. `api=2` 文档、`docs/plugin.md`、`AGENTS.md` 更新。
7. 提交；记录 commit hash 给插件工作区。

### Phase 2：bot_space 插件（新工作区）

1. 建独立仓库 `D:\Dev\bot_space`；`plugin.toml` `api=2`。
2. `core/space.py`：路径、slug、配额、原子写、审计；纯逻辑测试。
3. `core/tools.py` + `TOOLS`；工具 schema。
4. `pages/index.html` 只读面板 + `router` GET。
5. `tools/deploy-to-bot.ps1`，部署到 `plugins/bot_space`，写 `.deployed.json`。
6. 插件自检、pytest、ruff。
7. 部署到主项目，启用，私聊实测：模型自己决定 list/read/append。
8. 提交插件仓库；发布版本。

### Phase 3：L3

1. `ChatService` 公开 gate / run_agent。
2. 插件定时任务；预算/空闲/停用。
3. 每日 journal 实测。

---

## 10. 新工作区约定

- 插件源码：`D:\Dev\bot_space`（独立 git 仓库）。
- 部署副本：`D:\Dev\qq-bot-lite\plugins\bot_space`，**不要直接改副本**。
- 主项目核心改动：在 `D:\Dev\qq-bot-lite` 工作区做；插件工作区只依赖 `api=2` 契约，
  不复制 `app/sdk.py`。
- 插件测试可以用主项目 venv：

```powershell
$env:PYTHONPATH = 'D:\Dev\qq-bot-lite'
D:\Dev\qq-bot-lite\.venv\Scripts\python.exe -m pytest -q
```

- 部署脚本参考 `D:\Dev\whale_lexicon\tools\deploy-to-bot.ps1`，目标改成 `plugins\bot_space`，
  排除 `tests/`、`tools/`、`.git`、缓存，写 `.deployed.json`。
- 插件加载后默认**不启用**；主面板插件卡片手动打开。
- 删除插件时用现有删除弹窗的「连数据一起删」清空空间。

---

## 11. 跨工作区协作说明

就当前 DSH 工具面：

- 不同 workspace / 会话**没有自动的跨会话消息总线**；独立会话不会共享上下文，也不能直接
  给对方发消息。
- 能做到的协作：
  1. **共享文件系统 + git**：一个工作区写契约/提交，另一个工作区读同一份文件、拉同一个仓库。
  2. **子代理**：在一个会话里用 `subagent` 指定 `cwd`，让子代理在另一个目录干活；子代理结果
     回到父会话。但这是父子关系，不是两个独立会话对等聊天。
  3. **workflow**：在一个会话里编排多个子代理并行/串行，适合大范围审计或迁移。
- 因此建议：
  - 主项目工具协议在 qq-bot-lite 工作区完成并提交；
  - `bot_space` 工作区从该 commit 开始，按本文件实现插件；
  - 两边通过 `docs/bot-space.md` + git commit 同步，不依赖实时对话；
  - 若需要同时改两边，优先在一个父会话里用两个子代理，而不是开两个互不通信的会话。

---

## 12. 待确认

- 面板是否只读（当前设计是只读；删除数据走宿主生命周期）。
- 是否允许模型 `space_create` 任意标题，还是只允许 `space_append` 当天 journal/固定文件。
- L2 是否只在私聊开；群聊是否永远关闭。
- L3 的触发时间、每日预算、是否需要主动摘要（v1 建议不需要）。
- 工具名是否统一 `space_` 前缀；宿主重名时报错还是自动加插件前缀。
- `api=2` 的兼容策略：支持 `api<=2`，旧插件不强制升级。

---

## 13. 不做什么（明确）

- 不把 `space_*` 工具写进主项目核心。
- 不做用户 `/space` 命令。
- 不做 OS 沙箱，不承诺插件代码隔离。
- 不做 delete/overwrite/rename 的模型工具。
- 不做流式 tool_calls（第一期）。
- 不做主动 QQ 推送（第一期）。
- 不把工具对话写进持久历史。
- 不让插件绕过 `_llm_slot` 直接并发调模型。
