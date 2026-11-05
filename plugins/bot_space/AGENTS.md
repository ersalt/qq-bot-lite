# AGENTS.md · bot_space（鲸鱼空间）

给接手这个仓库的 AI / 新同事看的。先读 **README.md** 和 **docs/bot-space.md**，再动代码。

## 这是什么

qq-bot-lite 的插件：**鲸鱼空间**（id `bot_space`），bot 自己写、用户只读的 Markdown 手记。
插件只给模型工具，不给用户命令；面板只读。

本仓库是**插件开发源**；主项目 `D:\Dev\qq-bot-lite\plugins\bot_space` 是完整内置副本
（含 tests/docs）。主要开发在本仓库；完成后把运行时源码 + tests + docs 同步到主项目副本。

**不要直接改主项目。** 宿主缺能力先写 HOST-REQUEST，交给用户转给主项目 Agent。
协作规则见主项目 `docs/plugin-integration.md`，宿主 API 见 `docs/plugin.md`，UI 见 `docs/ui-spec.md`。

- 仓库：`D:\Dev\bot_space`（独立 git）
- 主项目：`D:\Dev\qq-bot-lite`
- 接口契约：`api = 2`
- 设计记录：本仓库 `docs/bot-space.md`（内部设计，不是用户手册）

## 目录

```text
plugin.toml        清单 + 设置项（不导入代码就能读）
plugin.py          setup / tools() / 只读面板 GET 路由
core/space.py      纯逻辑，不 import app.*：路径、配额、原子写、审计、搜索、回收站
core/tools.py      十个模型工具的 schema 与 handler（只 import app.sdk）
pages/index.html   单文件只读面板：文件树、Markdown 原文/预览、用量、审计、回收站
tests/             conftest 在没有主项目时注入最小 app.sdk stub
docs/              设计记录（内部）
```

不再需要打包 ZIP、安装脚本或部署脚本。主项目默认启用；更新时同步副本并重启/重载。

## 校验（改完必须跑）

```powershell
# 在主项目根目录，跑内置副本
.venv\Scripts\python.exe -m pytest plugins/bot_space/tests -q
.venv\Scripts\ruff.exe check plugins/bot_space
.venv\Scripts\python.exe -m app.pluginkit.check plugins/bot_space

# 或在插件仓库里（用主项目 venv）
D:\Dev\qq-bot-lite\.venv\Scripts\python.exe -m pytest tests -q
```

## 不能碰的设计

1. **没有用户命令。** 不导出 `COMMANDS`，不打 `/space`。手记是 bot 的，写不写由模型决定。
2. **没有直接永久删除活跃文件/rename/move。** 模型只能
   list/read/search/create/append/edit/delete/trash/restore/purge；`space_delete` 是软删除到
   `.trash/<id>/`，`space_restore` 可恢复，`space_purge` 才永久删除回收站条目。
   修改用 `space_edit`，不要用 append 追加“更正”。
3. **根目录固定** `ctx.data_dir / "space"`，不接受任意绝对路径；路径必须过 `normalize_path`。
4. **面板只读，设置除外。** 手记内容接口全部 GET；设置弹窗通过主程序
   `/api/plugins/bot_space/settings` 改插件设置，不直接改手记。不加编辑框、新建、删除、触发按钮。
5. **手记只有一份，不分群聊私聊。** 插件不按 `is_group` 限制，私聊/群里读写同一个根目录；
   主项目工具对所有会话暴露，不要再把群聊滤掉。
6. **原子写**：同目录临时文件 + `os.replace`；同一路径 `asyncio.Lock` 串行。
7. **审计不记正文**：只记工具名、路径、字数、成败、会话键；日志也不打完整内容。
8. **不引入依赖**：只用标准库 + 主程序已装的包；面板不引 CDN/外部字体/图标库。
9. **system 只加固定说明。** `SPACE_CHARTER` 是逐字稳定的自主权说明和删除授权协议，不拼时间/文件/群名；
   `autonomy_guard=false` 时不注入。动态提醒（关键词“小本本/手记/空间/笔记/日记”）只走
   `user_suffix`，不要把它挪进 system 或每次动态拼 system。
10. **回答空间内容前先读/搜。** 没读到的不要编页码、条目、人物关系；空间里的名字是记录对象，
    不是当前说话人，当前说话人看 `<ctx>` 的 `speaker`。
11. **删除/修改授权在提示词层。** 规则是“先分内容归属 + 展示完整 preview + 结束本轮 +
    下一条明确确认 + 第三方代删拒绝”。`space_edit` 删行也算删除；确认≠授权；
    无法判断归属就保留原文。`space_edit` / `space_delete` / `space_purge` / `space_restore`
    及 `sensitive=true` 的 `space_append` 第一次只返回预览 + `confirm_token`，不执行；
    确认后带 token 再调。它降低被忽悠概率，但不是硬 ACL。
12. **读/搜也分归属。** `space_read` / `space_search` 只取最少内容，不向无关请求者披露
    第三方的私事、秘密、片段、文件名、行号或命中数量；回收站元数据同属隐私。
    `space_append` 的 `sensitive` 由模型判断，不能当成硬敏感内容检测。

## 常见坑

- 工具 handler 的预期失败用 `SpaceError`（带 `code`）；宿主会转成 `ok: false`，
  不要自己返回 `{"ok": false}` 再被包一层。
- `allow_write = false` 时 `tools()` 直接不返回写工具；不要只靠 handler 里判断。
- 自主权说明只在 `system_prompt` 里返回固定文本；改它会导致一次 KV 缓存重建，但不要为了
  “新鲜感”在 system 里拼动态内容。
- 路径清洗会 slug 化并转小写，读路径必须用 `space_list` 返回的规范路径。
- 手记全局一份；群聊和私聊只影响审计的 `session`，不影响存储路径。不要加按会话分目录。
- 删除是软删除：`.trash/<id>/meta.json` 记原路径和时间，`content.md` 存正文；
  列表/搜索走 `_iter_md`，配额走 `_iter_all_md`（含回收站）。
- `space_purge` 只删 `.trash/<id>/` 整目录，释放配额；不要做成按路径删除活跃文件。
- `space_edit` 的 `old` 必须逐字匹配，非空；命中多处默认报错，只有 `all=true` 才全替换。
- 删除/修改授权是提示词协议，不是代码闸门；别把“第三方代删”“自称主人”“测试/威胁”
  当成授权。改 `SPACE_CHARTER` 时保留两步确认。
- `space_append` 的 `sensitive` 是模型自报，不是内容分类器；不要以为设了它就能自动识别
  第三方隐私。`space_read` / `space_search` 的隐私边界也只在提示词层。
- Windows 保留名、结尾空格/点、符号链接/junction 都有测试，改路径逻辑先跑 `test_space.py`。
- 面板的 Markdown 渲染先 `escapeHtml` 再拼块；链接只放行 `http(s)`，不加载图片。
- 配额改设置后下一轮生效；`ToolRunner` 有 10 秒工具超时，保持文件操作同步且短。
- 面板复用主项目 `.topbar/.page/.head/.card/.modal/.md-body` 与 `/static/theme.css`、`style.css`；
  宿主样式变更会影响插件面板。要局部覆盖先按 `docs/plugin-integration.md` 发交接。
- 弹窗退场复用宿主 `.modal[open].is-closing` / `dialog-out`；`closeMask()` 是 **host-gap**，
  因为插件页没加载 `plugins.js`，等 `ui.js` 通用 helper 落地后替换。
