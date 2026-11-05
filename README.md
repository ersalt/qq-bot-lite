# qq-bot-lite

本地优先的 QQ 机器人：OneBot 11 反向 WS 接入 SnowLuma，模型走 LM Studio。

```text
QQ ↔ SnowLuma ↔ qq-bot-lite(8790) ↔ LM Studio(1234)
```

- 群聊 + 私聊、人设、上下文裁剪、引用/@/分段、图片识别；
- 原生工具协议（`tool_calls` / `role=tool`），工具对所有会话开放；
- 插件系统 `api = 2`，两个内置插件默认启用：鲸语汇、BOT空间；
- 内置插件是完整副本（含 tests/docs，方便学习）；主要开发在各自插件项目，完成后同步进来；
- 纯原生前端，无构建链；配置和文档都在本机，不需要公网服务器。

## 文档

- [使用指南](docs/guide.md)：安装、SnowLuma、LM Studio、WebUI、插件、常见问题
- [设置说明](docs/settings.md)：每个 WebUI 设置项逐条解释
- [开发文档](docs/development.md)：架构、工具循环、插件开发、测试、发布
- [SDK 与 API](docs/api.md)：插件 SDK、清单、宿主 HTTP 接口
- [插件规范](docs/plugin.md) / [本地优先取舍](docs/local-first.md) / [界面规范](docs/ui-spec.md) / [日志格式](docs/log-spec.md)

启动后也可以从 WebUI 顶栏「文档」直接阅读；文档全部在仓库里，不依赖外部站点。

## 快速开始

双击 `启动.cmd`（`start.cmd` 是同一入口）。首次启动会自动创建 `.venv`、安装依赖，并从
`config/config.example.toml` 复制出 `config/config.toml`。

- 设置页：<http://127.0.0.1:8790/>
- 健康检查：<http://127.0.0.1:8790/healthz>
- OneBot 入口：`ws://127.0.0.1:8790/onebot/v11/ws`

配置、SnowLuma 接入、LM Studio 模型和插件设置的完整步骤见[使用指南](docs/guide.md)。

## 目录

```text
app/                 FastAPI、消息链路、上下文、模型客户端、插件宿主、WebUI
plugins/             内置插件：whale_lexicon / bot_space / _template
personas/            人设 Markdown
docs/                使用指南、开发文档、设计与规范
config/              config.toml（本机配置，不入库）+ 示例
data/                会话库、插件数据、图片副本（不入库）
logs/                每次启动一个日志，latest.txt 指向最新
tests/               主项目测试
```

## 校验

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m pytest plugins/whale_lexicon/tests -q
.\.venv\Scripts\python.exe -m pytest plugins/bot_space/tests -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m app.pluginkit.check plugins
```

## 版本与发布

版本号唯一来源是 `VERSION`，同步改 `CHANGELOG.md`。日常在 `develop` 开发，
发版时合并到 `main`、打附注 tag、发 GitHub Release。
