"""配置加载：config/config.toml 是唯一配置源（首次启动从 config.example.toml 复制）。"""

from __future__ import annotations

import logging
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.toml"
EXAMPLE_PATH = ROOT / "config" / "config.example.toml"
DEFAULT_PERSONA = ROOT / "personas" / "default.md"


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


@dataclass(slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8790
    log_level: str = "info"
    # 控制台每隔多少秒打一行状态（0 = 不打）
    status_interval: float = 300.0


@dataclass(slots=True)
class OneBotConfig:
    path: str = "/onebot/v11/ws"
    access_token: str = ""
    action_timeout: float = 15.0
    max_connections: int = 1


@dataclass(slots=True)
class LLMConfig:
    base_url: str = "http://127.0.0.1:1234/v1"
    api_key: str = "lm-studio"
    model: str = "qwen3.8-27b"
    # 当前生效的配置名，对应 config/models.toml 里的 [[profile]]。
    profile: str = ""
    # 软件侧上下文上限：0 = 不限制。填了就没法把 prompt 撑到模型窗口以外。
    max_context: int = 0
    # 采样参数。None = 不发送，用 LM Studio 里模型自己的设置（AstrBot 也是这样）。
    # 想强制指定就填数字，比如 temperature = 0.8。
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int = 0
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    timeout: float = 120.0
    # 流式：关掉就是等整段生成完再发。本地 3 秒就能出全文，分段前没必要早发半句。
    stream: bool = False
    # LM Studio 的思考档位：none / low / medium / high，留空 = 不发送（用模型模板默认）。
    # AstrBot 就是不发的，抓包确认过。填了就按填的发。
    reasoning_effort: str = ""
    # 思考超过这么多 token 就掐断连接，改用无思考模式重答（0 = 不限制）。
    # **只在 stream = true 时有效**：非流式拿不到思考增量，数不了也掐不断。
    thinking_budget: int = 0
    # 全局同时在飞的请求数上限，对应 LM Studio 的并行槽位数（实测它有 4 个槽位）。
    # 同一会话由 per-session 锁保证只有一条，这里限的是跨会话总数。
    max_concurrency: int = 4
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContextConfig:
    max_messages: int = 100
    max_tokens: int = 6000
    include_bot_replies: bool = True
    merge_same_role: bool = True
    # 只把“它接过的轮次”写进历史，群里跟它无关的闲话不进。
    # 实测全员闲聊全塞进去时模型思考 267~514 token（9~13 秒），
    # 只留问答轮次后能压到 100 上下（4~6 秒），和 AstrBot 的做法一致。
    only_addressed: bool = True
    # 历史行前面加 [HH:MM]，模型才知道“刚才/两小时前”
    timestamps: bool = True


@dataclass(slots=True)
class PersonaConfig:
    file: str = "personas/default.md"
    nickname: str = "小女仆"
    # 卡片顺序（面板里拖动排序后写回）。不在表里的按名字排在后面。
    order: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TriggerConfig:
    private_always: bool = True
    group_at: bool = True
    group_prefix: str = ""
    group_keywords: list[str] = field(default_factory=list)
    cooldown: float = 0.0


@dataclass(slots=True)
class ReplyConfig:
    segments: bool = True
    max_segments: int = 4
    segment_max_chars: int = 120
    segment_interval: float = 0.6
    quote: bool = True
    at_sender: bool = False
    # 把模型写的 @昵称 解析成真正的 at 段
    resolve_mentions: bool = True
    max_mentions: int = 3
    dedupe_window: float = 30.0


@dataclass(slots=True)
class ImagesConfig:
    enabled: bool = True
    max_per_message: int = 2
    # QQ 群里的原图动辄 5~10MB，4MB 的上限会把正常照片全挡掉（日志里就是
    # “图片超过 4000000 字节上限，跳过”）。反正下完就要缩到 max_side，
    # 这里只是防内存爆掉的上限，放宽到 20MB。
    max_bytes: int = 20_000_000
    max_side: int = 512
    # 历史里额外附带几张图（0 = 只处理当前这条）。
    # 2 张够答“刚才那张图里…”；每张约 250~400 token，加多了又贵又让前缀更难稳定。
    history_images: int = 2
    timeout: float = 15.0
    trust_env: bool = False


@dataclass(slots=True)
class CommandsConfig:
    """/xxx 命令层：插件导出 COMMANDS 就会被这里分发。"""

    prefix: str = "/"
    # 能用管理员命令的 QQ 号。留空 = 不限制（本地自用方便，群里慎用）。
    admins: list[str] = field(default_factory=list)
    timeout: float = 20.0


@dataclass(slots=True)
class ExtensionsConfig:
    enabled: list[str] = field(default_factory=list)
    # 额外插件目录（绝对路径）。插件项目可以放在仓库外面，这里登记一下就能加载。
    dirs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    onebot: OneBotConfig = field(default_factory=OneBotConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    reply: ReplyConfig = field(default_factory=ReplyConfig)
    images: ImagesConfig = field(default_factory=ImagesConfig)
    extensions: ExtensionsConfig = field(default_factory=ExtensionsConfig)
    commands: CommandsConfig = field(default_factory=CommandsConfig)

    @property
    def persona_path(self) -> Path:
        path = Path(self.persona.file)
        return path if path.is_absolute() else ROOT / path

    def summary(self) -> str:
        """启动时打印，避免“设置不透明”。"""
        llm = self.llm
        return "\n".join(
            [
                f"  反向 WS   : ws://{self.server.host}:{self.server.port}{self.onebot.path}",
                f"  鉴权 token: {'<未设置>' if not self.onebot.access_token else '<已设置>'}",
                f"  模型      : {llm.model} @ {llm.base_url} (stream={llm.stream})",
                f"  上下文    : 最近 {self.context.max_messages} 条 / 约 {self.context.max_tokens} tokens",
                f"  人设      : {self.persona_path}",
                f"  回复      : quote={self.reply.quote} at={self.reply.at_sender} "
                f"segments={self.reply.segments} (max {self.reply.max_segments})",
                f"  图片      : enabled={self.images.enabled} max_side={self.images.max_side}",
                f"  扩展      : {self.extensions.enabled or '<无>'}",
            ]
        )


def _merged(defaults: Any, data: dict[str, Any]) -> Any:
    """按 dataclass 字段名覆盖默认值，忽略未知键。"""
    kwargs = {}
    for f in defaults.__dataclass_fields__.values():  # type: ignore[attr-defined]
        if f.name in data and data[f.name] is not None:
            kwargs[f.name] = data[f.name]
    return type(defaults)(**kwargs)


def load_config(path: Path | None = None) -> Config:
    config_path = path or CONFIG_PATH
    raw: dict[str, Any] = {}
    # 首次运行：没有 config.toml 就从示例复制一份，用户照着注释改
    if path is None and not config_path.exists() and EXAMPLE_PATH.exists():
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(EXAMPLE_PATH, config_path)
            log.info("没有 config.toml，已从 %s 复制一份", EXAMPLE_PATH.name)
        except OSError:
            log.warning("复制示例配置失败，先用内置默认值启动", exc_info=True)
    if config_path.exists():
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)

    cfg = Config(
        server=_merged(ServerConfig(), _section(raw, "server")),
        onebot=_merged(OneBotConfig(), _section(raw, "onebot")),
        llm=_merged(LLMConfig(), _section(raw, "llm")),
        context=_merged(ContextConfig(), _section(raw, "context")),
        persona=_merged(PersonaConfig(), _section(raw, "persona")),
        trigger=_merged(TriggerConfig(), _section(raw, "trigger")),
        reply=_merged(ReplyConfig(), _section(raw, "reply")),
        images=_merged(ImagesConfig(), _section(raw, "images")),
        extensions=_merged(ExtensionsConfig(), _section(raw, "extensions")),
        commands=_merged(CommandsConfig(), _section(raw, "commands")),
    )
    return cfg



