"""配置加载：config/config.toml 是唯一配置源。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.toml"
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
    temperature: float = 0.8
    top_p: float = 0.95
    max_tokens: int = 1024
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    timeout: float = 120.0
    stream: bool = True
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContextConfig:
    max_messages: int = 100
    max_tokens: int = 6000
    include_bot_replies: bool = True
    merge_same_role: bool = True


@dataclass(slots=True)
class PersonaConfig:
    file: str = "personas/default.md"
    nickname: str = "小女仆"


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
    at_sender: bool = True
    dedupe_window: float = 30.0


@dataclass(slots=True)
class ImagesConfig:
    enabled: bool = True
    max_per_message: int = 2
    max_bytes: int = 4_000_000
    max_side: int = 512
    history_images: int = 0
    timeout: float = 15.0
    trust_env: bool = False


@dataclass(slots=True)
class ExtensionsConfig:
    enabled: list[str] = field(default_factory=list)


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
    )
    return cfg
