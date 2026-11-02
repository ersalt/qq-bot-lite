# AstrBot 源码对照笔记

不是"抄 AstrBot"，是把它的取舍读明白，再判断哪些适合本地小模型。
读的是 `AstrBotDevs/AstrBot`，`main` 分支 `6c94285`（2026-11-02）。

## 一、群聊上下文：一个只追加的文本块

`astrbot/builtin_stars/astrbot/group_chat_context.py` 是整个"群聊记忆"的核心，做法比想象的简单：

```python
GROUP_HISTORY_HEADER = (
    "<system_reminder>"
    "You are in a group chat. "
    "Belows are group chat context after your last reply:\n"
    "--- BEGIN CONTEXT---\n"
)
```

要点：

1. **每条群消息先存进内存 deque**，格式是 `[昵称/HH:MM:SS]: 文本`，组件就地渲染成
   `[Image]`、`[At: 名字]`、`[Quote(昵称: 内容)]`、`[Shared Card: 标题; 描述; 链接]`。
2. **唤醒模型时，把"上一条回复之后"的所有消息拼成一个大文本块**，塞进
   `req.extra_user_content_parts` —— 也就是**当前这条 user 消息**里，而**不是**塞进
   `contexts`（对话历史）里。
3. **塞完立刻从 deque 删掉**（`records.clear(); records.extend(remaining)`）。
   所以同一条消息只会被送进模型一次，不会重复。
4. 上限是 `DEFAULT_GROUP_MESSAGE_MAX_CNT = 1000` 条，**没有 token 预算**，
   爱多长多长 —— 反正云端模型 128k 上下文吃得下。
5. 有人在 @ 自己时，在行首插一个 `⚠️[DIRECTED AT YOU]` 标记。

**这条设计对我们的意义**：它天然是"两层"的 —— 一层是结构化的对话历史（`contexts`，
带 user/assistant 角色），一层是纯文本的未读群聊块。前者按轮数截断，后者按条数截断，
互不干扰。我们目前是一层：全部拍平成 user/assistant 轮次。结构上更规整，但**每条群消息
都要占一个 user 轮次**，token 花得比它多。

## 二、对话历史与压缩：按轮丢，或者叫模型自己摘要

`astrbot/core/agent/context/`：

| 配置 | 默认值 | 含义 |
|---|---|---|
| `max_context_length` | 50 | 保留最近 50 **轮**（一轮 = 用户 + 助手，≈100 条消息） |
| `truncate_turns` | 1 | 触发时一次丢 1 轮（不是全清） |
| `compression_threshold` | 0.82 | 上下文 token 达到模型上限 82% 才动手 |
| `fallback_max_context_tokens` | 128000 | 查不到模型元数据时按 12.8 万算 |

两种策略二选一：

- `TruncateByTurnsCompressor`：按轮丢弃，默认策略，**不花任何额外请求**。
- `LLMSummaryCompressor`：把旧消息发给模型做摘要，保留最近 15% 原文。

截断后会 `fix_messages()`，保证 tool_call 和 tool_result 成对、system 在最前面、
第一个非 system 消息是 user —— 这几点是 OpenAI 兼容接口的硬要求，我们的
`merge_adjacent` 已经在做同类的事（只合并 assistant，不动 user 顺序）。

token 估算（`token_counter.py`）：中文 0.6 / 字符，其他 0.3 / 字符，图片按 765 像素级估。
**中文按 0.6 是偏低的**，等于给自己留了余地。

## 三、动态信息放哪儿：一律放 user，system 保持纯净

`astrbot/core/astr_main_agent/request.py::append_system_reminders`：

```python
if cfg.get("identifier"):
    system_parts.append(f"User ID: {user_id}, Nickname: {user_nickname}")
if cfg.get("group_name_display") and event.message_obj.group_id:
    system_parts.append(f"Group name: {group_name}")
if cfg.get("datetime_system_prompt"):
    system_parts.append(f"Current datetime: {current_time}, Weekday: {weekday}")
...
req.extra_user_content_parts.append(TextPart(text=system_content))
```

名字叫 `system_reminder`，实际**进的是 user content**。system prompt 里只有人设、
工具说明、搜索说明这些静态内容。

这一点我们已经在做了（`<ctx>` 块），算是撞上了同一个答案。区别是它连"当前时间"都放
user 里，我们也是 —— 因为 llama.cpp 的前缀缓存比云端更敏感，改一个字节就全段重算。

## 四、请求参数：能不给就不给

`astrbot/core/provider/sources/openai_source.py`：

- **默认不发 `max_tokens`**（只有少数 provider 走兼容路径时才补 8192）。
- 发 `stream_options={"include_usage": True}`（为了拿到 usage）。
- 有工具时 `tool_choice="auto"`。
- 不在白名单里的参数一律进 `extra_body`，再合并 `custom_extra_body`。
- `reasoning_effort` 只在 Ollama 兼容分支里被显式设成 `"none"`，其他地方不碰。

**不含 `max_tokens` 这件事值得注意**：它把"说多长"完全交给模型和模板，
代价是长尾不可控 —— 云端按 token 计费但延迟不在乎，本地在乎。
我们反过来，`max_tokens=1024` 是一道硬闸，配套 `thinking_budget` 兜底。

## 五、我们没照抄的地方，以及原因

| AstrBot 的做法 | 我们的做法 | 为什么 |
|---|---|---|
| 群历史拼成一个大文本块塞 user | 拍平成 user/assistant 轮次 | 结构化角色更不容易串台；代价是 token 多一些 |
| 群历史上限 1000 条，无 token 预算 | 100 条 + 6000 token 双闸 | 本地上下文只有 32k，撑不住 |
| 不传 `max_tokens` | `max_tokens=1024` + 思考预算 | 本地延迟就是钱，必须给长尾设上限 |
| 上下文到 82% 才截断 | 锚点窗口 60%~125% 就推进 | 我们要的是"前缀尽量别变"，不是"尽量晚截" |
| 压缩可调 LLM 摘要 | 只做按轮丢弃 | 摘要要额外一次本地请求，本地做不起 |
| 时间/群名/昵称进 user content | 同样进 user content | 一致，都是为了缓存 |

## 六、能直接学的三件小事

1. **`⚠️[DIRECTED AT YOU]` 标记**：它用显式标记告诉模型"这条是冲你来的"，
   比让模型自己从 at 段推断更稳。我们的 `<ctx>` 里 `speaker` 已经是当前发言人了，
   暂时不需要，但"只 @ 不说话"那种空消息可以借这个思路。
2. **引用的正文会被截断到 200 字**（`_MAX_REPLY_TEXT_LENGTH`）后内联，
   避免一条长引用把上下文撑爆。我们目前只写"（他引用了你之前说的话）"，没带正文 ——
   这是省 token 但丢信息的取舍，值得以后加个开关。
3. **`EstimateTokenCounter` 按字符类型给系数**，中文 0.6、emoji 2.0。
   我们的 `estimate_text_tokens` 可以对齐一下 emoji 的系数，避免长表情把预算算漏。
