"""设置面板的字段描述。

一份 schema 同时供页面渲染和保存校验用，字段顺序就是页面顺序。
`restart=True` 的项在启动时就被组件抓走了（httpx 客户端的 base_url、
监听端口等），只能改完重启生效。
"""

from __future__ import annotations

from typing import Any

SUFFIX_MONO = {"max_tokens", "max_messages", "port", "max_bytes"}


def _f(
    key: str,
    label: str,
    kind: str = "text",
    help: str = "",
    **extra: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = {"key": key, "label": label, "type": kind, "help": help}
    item.update(extra)
    return item


SECTIONS: list[dict[str, Any]] = [
    {
        "id": "server",
        "title": "服务与访问",
        "desc": "反向 WebSocket 监听地址。端口改了要重启进程，SnowLuma 那边也要跟着改。",
        "fields": [
            _f("server.host", "监听地址", help="一般只在本机用，保持 127.0.0.1", restart=True),
            _f("server.port", "监听端口", "number", min=1, max=65535, restart=True),
            _f(
                "server.log_level",
                "日志级别",
                "select",
                options=["debug", "info", "warning", "error"],
                help="排查问题时用 debug，平时 info",
            ),
            _f(
                "server.status_interval",
                "状态行间隔（秒）",
                "number",
                min=0,
                max=3600,
                step=0.5,
                help="控制台每隔多久打一行运行状态，0 = 不打",
                restart=True,
            ),
        ],
    },
    {
        "id": "onebot",
        "title": "OneBot 连接",
        "desc": "SnowLuma 连进来时用的地址、鉴权和超时。",
        "fields": [
            _f("onebot.path", "WS 路径", help="SnowLuma 里填 ws://127.0.0.1:8790 + 这个路径", restart=True),
            _f(
                "onebot.access_token",
                "鉴权 token",
                "password",
                help="留空 = 不校验。改完要重启",
                restart=True,
            ),
            _f(
                "onebot.action_timeout",
                "动作超时（秒）",
                "number",
                min=1,
                max=120,
                step=0.5,
                help="等发送接口回执的时间",
            ),
            _f("onebot.max_connections", "最大连接数", "number", min=1, max=8, restart=True),
        ],
    },
    {
        "id": "llm",
        "title": "模型",
        "desc": "本地 LM Studio 的 OpenAI 兼容端点。留空的项不会出现在请求体里。",
        "fields": [
            _f("llm.base_url", "接口地址", help="本机直连，别填代理地址", restart=True),
            _f("llm.api_key", "API Key", "password", help="LM Studio 不校验，随便填", restart=True),
            _f("llm.model", "模型标识", help="必须和 LM Studio 里已加载的模型名完全一致"),
            _f(
                "llm.max_context",
                "最大上下文（软件约束）",
                "number",
                min=0,
                max=1048576,
                optional_zero=True,
                help="0 = 不限制。填了就按它的 50% 当 prompt 预算，prompt 撑不到别处去",
            ),
            _f(
                "llm.temperature",
                "temperature",
                "number",
                min=0,
                max=2,
                step=0.05,
                optional=True,
                help="留空 = 不发送，用 LM Studio 里模型自己的设置",
            ),
            _f(
                "llm.top_p",
                "top_p",
                "number",
                min=0,
                max=1,
                step=0.01,
                optional=True,
                help="留空 = 不发送",
            ),
            _f(
                "llm.max_tokens",
                "max_tokens",
                "number",
                min=0,
                max=32768,
                optional_zero=True,
                help="0 = 不发送这个字段，让模型和模板自己收尾",
            ),
            _f(
                "llm.reasoning_effort",
                "思考档位",
                "select",
                options=["", "none", "low", "medium", "high"],
                help="留空 = 不发送。现在靠人设里的篇幅约束控制思考量",
            ),
            _f(
                "llm.stream",
                "流式返回",
                "bool",
                help="关掉就是整段生成完再发。非流式下 thinking_budget 失效",
            ),
            _f(
                "llm.thinking_budget",
                "思考量上限",
                "number",
                min=0,
                max=8192,
                optional_zero=True,
                help="客户端保险丝，边收边数，超了掐断换无思考重答。0 = 关闭，只在流式下有效",
            ),
            _f(
                "llm.timeout",
                "请求超时（秒）",
                "number",
                min=5,
                max=1800,
                help="等模型首个响应的上限",
            ),
            _f(
                "llm.max_concurrency",
                "并发请求上限",
                "number",
                min=1,
                max=16,
                help="对应 LM Studio 的并行槽位数。同一会话永远只有一条",
            ),
            _f(
                "llm.presence_penalty",
                "presence_penalty",
                "number",
                min=-2,
                max=2,
                step=0.1,
                optional_zero=True,
                help="0 = 不发送",
            ),
            _f(
                "llm.frequency_penalty",
                "frequency_penalty",
                "number",
                min=-2,
                max=2,
                step=0.1,
                optional_zero=True,
                help="0 = 不发送",
            ),
        ],
    },
    {
        "id": "context",
        "title": "上下文",
        "desc": "送进模型的历史怎么攒。条数和 token 双闸，超了从最旧的丢。",
        "fields": [
            _f(
                "context.max_messages",
                "最近消息条数",
                "number",
                min=10,
                max=500,
                help="锚点窗口会把它浮动在 60%~125% 之间，不是每条都挪",
            ),
            _f(
                "context.max_tokens",
                "token 预算",
                "number",
                min=256,
                max=60000,
                optional_zero=True,
                help="估算值。超了一次退到 60%，避免前缀每轮位移",
            ),
            _f(
                "context.only_addressed",
                "只记被接过的轮次",
                "bool",
                help="群里跟它无关的闲话不进历史，只额外保留最后一段还没回的。关掉会明显变慢",
            ),
            _f("context.include_bot_replies", "包含自己的回复", "bool"),
            _f("context.merge_same_role", "合并连续同角色", "bool", help="只合并 assistant，user 一律独立"),
            _f("context.timestamps", "历史行加时间戳", "bool", help="格式 [HH:MM]，模型才能判断“刚才”"),
        ],
    },
    {
        "id": "persona",
        "title": "人设",
        "desc": "人设文件是纯 Markdown，改完即时生效，不用重启。",
        "fields": [
            _f("persona.nickname", "兜底昵称", help="拿不到群名片时用这个名字"),
        ],
    },
    {
        "id": "trigger",
        "title": "触发规则",
        "desc": "什么情况下它才开口。",
        "fields": [
            _f("trigger.private_always", "私聊必回", "bool"),
            _f("trigger.group_at", "被 @ 时回复", "bool"),
            _f("trigger.group_prefix", "指令前缀", help="留空 = 不用前缀触发，比如填 #"),
            _f(
                "trigger.group_keywords",
                "关键词",
                "list",
                help="命中任意一个就回复，逗号分隔；留空 = 不启用",
            ),
            _f(
                "trigger.cooldown",
                "冷却（秒）",
                "number",
                min=0,
                max=600,
                step=0.5,
                optional_zero=True,
                help="同一会话两次回复的最小间隔，0 = 不冷却",
            ),
        ],
    },
    {
        "id": "reply",
        "title": "回复方式",
        "desc": "怎么把回复发回群里。",
        "fields": [
            _f("reply.quote", "引用回复", "bool", help="引用触发它的那条消息"),
            _f("reply.at_sender", "@ 被回复的人", "bool", help="默认关，每条都 @ 会刷屏"),
            _f("reply.segments", "长回复分段", "bool"),
            _f("reply.max_segments", "最多几段", "number", min=1, max=10),
            _f("reply.segment_max_chars", "每段字数", "number", min=20, max=1000),
            _f(
                "reply.segment_interval",
                "分段间隔（秒）",
                "number",
                min=0,
                max=10,
                step=0.1,
                help="模拟打字停顿，0 = 连发",
            ),
            _f(
                "reply.resolve_mentions",
                "把 at 昵称变成真 @",
                "bool",
                help="模型写的 “at 昵称” 会查群名片换成真正的 at 段",
            ),
            _f("reply.max_mentions", "单条最多 @ 几个", "number", min=0, max=10),
            _f(
                "reply.dedupe_window",
                "去重窗口（秒）",
                "number",
                min=0,
                max=600,
                optional_zero=True,
                help="这段时间内相同内容不重复发，0 = 不去重",
            ),
        ],
    },
    {
        "id": "images",
        "title": "图片",
        "desc": "收到图会下载、缩到指定边长再送模型。小图原样送，不重编码。",
        "fields": [
            _f("images.enabled", "启用识图", "bool"),
            _f("images.max_per_message", "单条最多几张", "number", min=1, max=9),
            _f(
                "images.max_bytes",
                "单张大小上限（字节）",
                "number",
                min=100_000,
                max=100_000_000,
                step=1_000_000,
                help="QQ 原图常见 5~10MB，别设太小",
            ),
            _f(
                "images.max_side",
                "最长边像素",
                "number",
                min=256,
                max=2048,
                step=64,
                help="1024 更清楚但更费 token；带字截图建议 768 以上",
            ),
            _f(
                "images.history_images",
                "历史里额外带几张",
                "number",
                min=0,
                max=4,
                optional_zero=True,
                help="0 = 只处理当前这条。带 2 张就够答“刚才那张图里有什么”",
            ),
            _f("images.timeout", "下载超时（秒）", "number", min=3, max=120, restart=True),
            _f(
                "images.trust_env",
                "下载走系统代理",
                "bool",
                help="QQ 图床如果必须走代理才下得动，就打开。改完要重启",
                restart=True,
            ),
        ],
    },
]


# 模型那组字段分两档：基础在前，进阶隔一个空行。其余组一律 basic。
_ADVANCED_KEYS = {
    "llm.temperature",
    "llm.top_p",
    "llm.max_tokens",
    "llm.reasoning_effort",
    "llm.stream",
    "llm.thinking_budget",
    "llm.timeout",
    "llm.max_concurrency",
    "llm.presence_penalty",
    "llm.frequency_penalty",
}
for _section in SECTIONS:
    for _field in _section["fields"]:
        _field["group"] = "advanced" if _field["key"] in _ADVANCED_KEYS else "basic"
