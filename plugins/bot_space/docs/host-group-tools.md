# 主项目改动：让工具在群聊也可用（历史记录）

> 这是内置化之前的手动改动记录，**现在不需要再照做**：插件已随主仓库内置，
> 主项目也已对所有会话暴露工具。
> 2026-11-05：主项目已按本文完成并验证（77 passed），下面保留作变更记录/回滚参考。
> 目标：手记只有一份，私聊/群聊共用，工具不按会话类型分流。

## 1. 现在为什么群聊用不了

主项目 `app/core/pipeline.py` 里有一段硬编码：

```python
def _tools_for(self, event: MessageEvent) -> dict[str, ToolEntry]:
    """工具只在私聊暴露；群聊永远不给空间工具。"""
    if event.is_group:
        return {}
    return self.extensions.collect_tools()
```

插件侧本身没有这个限制：

- `BotSpace.tools()` 返回工具表，不判断 `is_group`；
- handler 只认 `session_key`，存储根目录是 `ctx.data_dir / "space"`，全局一份；
- 审计记录 `session`，群聊是 `group:<群号>`，私聊是 `private:<QQ>`，但读写同一份手记。

所以把群聊短路去掉，群聊就能用同一套工具。

## 2. 最小改动

```diff
-    def _tools_for(self, event: MessageEvent) -> dict[str, ToolEntry]:
-        """工具只在私聊暴露；群聊永远不给空间工具。"""
-        if event.is_group:
-            return {}
-        return self.extensions.collect_tools()
+    def _tools_for(self, event: MessageEvent) -> dict[str, ToolEntry]:
+        """工具对所有会话暴露；手记是 bot 的一份，不分群聊私聊。"""
+        return self.extensions.collect_tools()
```

`event` 参数保留：以后要按会话/权限过滤时还用得上。

## 3. 必须一起改的地方

1. `tests/test_tools.py::test_tools_are_private_only` 现在断言群聊返回 `{}`，改完会失败。
   要么改成“群聊也能拿到工具”，要么删掉这个测试。
2. `docs/plugin.md` 第 6.1 节、`AGENTS.md` 里“当前内核只在私聊把工具交给模型；
   群聊不暴露”的说明要同步改成“所有会话可用”。
3. 如果还有别的插件导出 `TOOLS`，这次改动会**一起**在群聊暴露；先确认它们是否适合群聊。
   当前只有 `bot_space` 有工具。

## 4. 风险

- **群聊写入**：群里任何人都能诱导模型往手记里写。`allow_write` 是全局开关，
  做不到“群里只读、私聊可写”；要这种粒度，得在宿主加按会话/权限过滤。
- **prompt 开销**：工具 schema 会进入群聊的每次请求，token 增加；工具循环只有非流式，
  群聊回复可能变慢。
- **审计**：群聊调用记成 `group:<群号>`，私聊记成 `private:<QQ>`；路径是同一份。
- 如果模型被群友恶意引导，它仍只能写固定的 `.md` 根目录，不会碰项目其他文件；
  但仍可能污染手记或写入无意义内容。

## 5. 验证

改完在主项目跑：

```powershell
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\ruff.exe check .
.venv\Scripts\python.exe -m app.pluginkit.check plugins
```

再在私聊和群里各试一次：

- 私聊说“记一下明天买猫粮”，应能追加到 `notes/` 或新建文件；
- 群里说“把刚才说的记一下”，模型应能调用 `space_append`，审计 `session` 是 `group:...`；
- 面板里能看到同一批文件，说明确实是**同一份手记**。
