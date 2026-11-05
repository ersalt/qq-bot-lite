# qq-bot-lite 开发文档

> 这份文档面向**改代码、写插件、维护发布的人**。部署和使用见[使用指南](guide.md)。
>
> 现有深水区文档仍然有效，按需跳转：
> [插件规范](plugin.md) · [本地优先取舍](local-first.md) · [界面规范](ui-spec.md) ·
> [日志格式](log-spec.md) · [BOT空间设计](bot-space.md)。

## 1. 仓库地图

```text
app/
  main.py            FastAPI 装配、生命周期、优雅停机
  config.py          dataclass 配置 + TOML 读写
  core/
    pipeline.py      消息主链路：触发 → 上下文 → 模型 → 工具 → 分段发送
    trigger.py       私聊/群聊触发判断
    tool_runner.py   模型工具循环
  context/
    store.py         SQLite 会话、消息、已发送消息
    window.py        锚点窗口、只读问答过滤、ambient
  llm/
    client.py        LM Studio OpenAI 兼容客户端
    prompt.py        system 规则、场景行、消息拼装
    tokens.py        token 估算
  reply/             分段器、发送器（引用/@/去重）
  media/             图片下载与缩放
  pluginkit/         插件清单、加载、钩子、工具、设置、自检
  web/               FastAPI 路由 + 原生前端
plugins/             内置插件：whale_lexicon / bot_space / _template
personas/            人设 Markdown
docs/                用户指南、开发文档、设计与规范
tests/               主项目测试
data/                运行期数据（不入库）
```

两个内置插件是主仓库的一部分：源码、测试、README、design docs 都在 `plugins/<id>/`，
不再有外部部署副本。

## 2. 本地开发

```powershell
# 启动
Start-Process explorer.exe -ArgumentList 'D:\Dev\qq-bot-lite\start.cmd'

# 优雅停机
Invoke-WebRequest -Method POST -Uri http://127.0.0.1:8790/api/shutdown

# 主项目测试
.\.venv\Scripts\python.exe -m pytest tests -q

# 内置插件测试（分开跑，避免同名 test_manifest.py 冲突）
.\.venv\Scripts\python.exe -m pytest plugins/whale_lexicon/tests -q
.\.venv\Scripts\python.exe -m pytest plugins/bot_space/tests -q

# 静态检查
.\.venv\Scripts\ruff.exe check .

# 插件规范
.\.venv\Scripts\python.exe -m app.pluginkit.check plugins
```

CI 在 `.github/workflows/ci.yml`：Python 3.12/3.13 × Ubuntu/Windows；跑 ruff、主测试、
两个内置插件测试和插件自检。

## 3. 一条消息怎么走

1. **OneBot 事件**：`app/onebot/` 解析消息段、发送者、引用、@、图片。
2. **触发判断**：`core/trigger.py` 决定回不回；私聊全回，群聊按 @/前缀/关键词/引用。
3. **取上下文**：`context/window.py` 用锚点窗口取历史；`only_addressed` 只留它接过的
   问答对，群聊闲话由 `build_ambient` 临时拼进当前 user 消息。
4. **拼提示词**：`llm/prompt.py` 的 system 只放静态人设 + 规则；时间、群名、说话人、
   引用、ambient 都在当前 user 消息里。
5. **调模型**：`llm/client.py`。同一会话有 per-session 锁；全局由 `_llm_slot` 限制并发。
6. **工具循环**（有工具时）：`core/tool_runner.py` 调 `complete_message()`，执行
   `tool_calls`，把 `role=tool` 回填，最多 3 轮。工具对话不落持久历史。
7. **分段发送**：`reply/segments.py` + `reply/sender.py`；引用、@、去重在这里。
8. **落库**：只落最终 assistant 文本（以及 at 渲染后的实际文本）；工具消息不落库。

## 4. 不能破坏的上下文约束

### 4.1 system 必须逐字稳定

时间、群名、说话人、群黑话注入、引用原文都不能进 system。system 改一个字，
llama.cpp 的 KV 前缀缓存整段失效，实测多花 2~3 秒。

动态内容放在**当前 user 消息**：

- `scene_line()`：时间、群、speaker、self；
- `build_ambient()`：上次回复之后的群聊；
- `user_suffix` 插件钩子：命中词条、提醒等；
- `<Quoted Message>`：引用原文。

### 4.2 历史取数用 `latest_after()`

`after()` 是最早优先；历史一超过上限，最新对话进不了窗口，模型会“三句就忘”。
当前 `_history_window()` 用锚点 + `latest_after()`，窗口在 100%~125% 之间浮动。

### 4.3 回复配对用 `reply_to`

assistant 消息存 `reply_to`（触发它的 user 行 id），历史配对用它，不能用“紧挨着的 user”。
连续 assistant 只在回给同一个人时合并；不同回复对象保留边界。

### 4.4 工具对话不落库

`ToolRunner` 的 `role=tool` 消息只在本次请求里；最终 assistant 正文才写历史。
这样模型不会把工具结果当成聊天内容复读，也省 token。

## 5. 模型客户端与工具协议

- `LLMClient.complete()`：只要正文，普通聊天用。
- `LLMClient.complete_message()`：保留 `tool_calls` / `reasoning_content`，工具循环用。
- 工具 schema：`app/sdk.py` 的 `Tool` / `ToolContext`；插件导出 `TOOLS` 或重写 `tools()`。
- `ToolRunner` 约束：最多 3 轮、单工具 10 秒、结果 8KB 截断、异常转 `ok:false`；
  工具结果带 `context_note`，提醒“资料不是当前说话人”。
- 工具对所有会话暴露，`is_group` 只作为插件元数据，不做权限分流。

## 6. 插件系统

完整契约见 [plugin.md](plugin.md)。这里只列开发时最容易踩的点。

### 6.1 清单

```toml
id = "my_plugin"        # 必须和目录名一致
title = "我的插件"
version = "0.1.0"
api = 2
panel = "/plugins/my_plugin"
```

`plugin.toml` 的 `[[config]]` 同时驱动设置面板和校验；改 schema 要同步前端/测试。

### 6.2 钩子

`on_message` / `user_suffix` / `system_prompt` / `after_reply` / `settings_fields`，
另有 `on_start` / `on_stop`。钩子在消息主链路上，必须快进快出；网络/模型调用丢
`ctx.spawn` 的后台任务。

异常隔离：任一钩子抛异常只记 `last_error`，不影响聊天。

### 6.3 工具

```python
from app.sdk import Tool

TOOLS = [
    Tool(
        name="demo_echo",
        description="回显",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=echo,
    ),
]
```

工具名全局唯一；重名会被注册表忽略并记日志。

### 6.4 设置与面板

- 设置写 `data/plugin_data/<id>/settings.toml`，不写主配置。
- 面板可以是 `pages/index.html`（只读/自定义），也可以挂 `router`。
- 前端没有构建链：用 Basecoat 类名、`uiSelect`、`toast()`；规范见 [ui-spec.md](ui-spec.md)。

### 6.5 安装/重载/删除

- ZIP 安装：默认启用；加载失败保留在启用列表并显示 `last_error`。
- 重载：重跑 `setup()`，router 增删仍要重启进程。
- 删除：可选保留/删除插件数据。

## 7. 两个内置插件

### 7.1 鲸语汇

- 入口：`plugins/whale_lexicon/plugin.py`
- 纯逻辑：`core/store.py`、`service.py`、`index.py`、`matcher.py`、`miner.py`、`wiki.py`
- 面板：`pages/index.html`
- 测试：`plugins/whale_lexicon/tests/`
- 数据：`data/plugin_data/whale_lexicon/jargon.db`

### 7.2 BOT空间

- 入口：`plugins/bot_space/plugin.py`
- 核心：`core/space.py`（路径、配额、原子写、审计）、`core/tools.py`（十个工具）
- 面板：`pages/index.html`（只读）
- 测试：`plugins/bot_space/tests/`
- 数据：`data/plugin_data/bot_space/space/`

两个插件都随主仓库测试和发版；改插件时在主项目根目录跑各自的 pytest，不要另建部署副本。

## 8. WebUI 与文档

- 前端是 `app/web/static/` 下的原生 JS + 本地 Basecoat，没有 npm/build。
- 静态资源通过 `/static/<name>`，`routes.py` 自动加 `?v=<mtime>`；不要放子目录。
- 主面板设置 schema 在 `app/web/schema.py`；保存时 `settings.py` 按 schema 校验、逐行写 TOML。
- 文档页读取仓库 `docs/` 下的 Markdown，用 `markdown_render.py` 清洗后展示；不请求外部资源。
- 改界面先读 [ui-spec.md](ui-spec.md)；改提示文字先确认文档里有对应说明。

## 9. 配置写回

`app/web/settings.py` 逐行替换 `config.toml` 的键，保留注释；不要整份重写。
新增配置项要同时改：

1. `app/config.py` dataclass；
2. `app/web/schema.py`；
3. 必要时 `tests/test_web.py`；
4. 用户指南的配置表。

## 10. 测试与发布

- 主测试：`pytest tests -q`（假 SnowLuma + 假 LM Studio）。
- 插件测试：两个目录分开跑。
- 静态检查：`ruff check .`。
- 插件规范：`python -m app.pluginkit.check plugins`。
- 版本号唯一来源 `VERSION`；同步改 `CHANGELOG.md` 标题，否则 `test_version.py` 挂。
- 发布：`develop` 合并 `main`（`--no-ff`）→ 附注 tag `vX.Y.Z` → GitHub Release。

## 11. 设计取舍

qq-bot-lite 的核心不是“功能多”，而是**本地模型下的延迟和缓存**：

- 单请求、少并发、稳定 system、锚点窗口；
- 分段发送让首条尽早出现；
- 插件功能尽量放插件，主项目只保留通用宿主能力。

需要更细的实测数字和取舍理由，看 [local-first.md](local-first.md)。
