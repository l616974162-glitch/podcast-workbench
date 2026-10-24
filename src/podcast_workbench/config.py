"""加载项目配置，不修改进程环境，不输出敏感值。"""

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET_NAMES = ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY")
ENV_REFERENCE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


class ConfigError(ValueError):
    """可以向用户展示的配置错误。"""


class ConfigSection(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, str_strip_whitespace=True)


class LLMConfig(ConfigSection):
    provider: Literal["deepseek"]
    base_url: HttpUrl
    model: str = Field(min_length=1)
    api_key: SecretStr
    timeout_seconds: float = Field(gt=0, le=300)
    max_output_tokens: int = Field(gt=0)
    structured_output_retries: int = Field(ge=0, le=1)


class TTSConfig(ConfigSection):
    protocol: Literal["qwen_tts"]
    endpoint: HttpUrl
    model: str = Field(min_length=1)
    api_key: SecretStr
    host_voice: str = Field(min_length=1)
    guest_voice: str = Field(min_length=1)
    language_type: Literal["Chinese"]
    max_text_chars: int = Field(gt=0, le=600)
    max_audio_bytes: int = Field(gt=0)
    timeout_seconds: float = Field(gt=0, le=300)


class PipelineConfig(ConfigSection):
    nodes: tuple[Literal["researcher"], Literal["writer"], Literal["reviewer"]]
    recursion_limit: int = Field(ge=5, le=50)


class AgentConfig(ConfigSection):
    prompt: str = Field(min_length=1)


class AgentsConfig(ConfigSection):
    researcher: AgentConfig
    writer: AgentConfig
    reviewer: AgentConfig


class RuntimeConfig(ConfigSection):
    max_source_chars: int = Field(gt=0)
    output_dir: Path
    log_dir: Path
    database_path: Path
    ffmpeg_path: str


class Settings(ConfigSection):
    llm: LLMConfig
    tts: TTSConfig
    pipeline: PipelineConfig
    agents: AgentsConfig
    app: RuntimeConfig

    def missing_secrets(self) -> list[str]:
        keys = (self.llm.api_key, self.tts.api_key)
        return [name for name, key in zip(SECRET_NAMES, keys, strict=True)
                if not key.get_secret_value().strip()]


def _expand(value: object, environment: dict[str, str]) -> object:
    if isinstance(value, dict):
        return {key: _expand(item, environment) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item, environment) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in environment:
            raise ConfigError(f"缺少环境变量：{name}")
        return environment[name]

    return ENV_REFERENCE.sub(replace, value)


def load_config(
    path: Path, *, env_file: Path | None = None, require_secrets: bool = False
) -> Settings:
    """优先级：进程环境 > 本地 .env > 仓库 .env.example 非敏感默认值。"""
    path = path.resolve()
    environment = {
        name: value for name, value in dotenv_values(
            PROJECT_ROOT / ".env.example", interpolate=False
        ).items() if value is not None and name not in SECRET_NAMES
    }
    environment.update(dict.fromkeys(SECRET_NAMES, ""))
    environment.update({
        name: value for name, value in dotenv_values(
            env_file if env_file is not None else path.parent / ".env", interpolate=False
        ).items() if value is not None
    })
    environment.update(os.environ)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ConfigError("无法读取配置，请检查文件是否存在及 YAML 格式。") from None
    if not isinstance(raw, dict):
        raise ConfigError("配置必须是 YAML 对象。")
    for section, name in zip(("llm", "tts"), SECRET_NAMES, strict=True):
        entry = raw.get(section)
        if not isinstance(entry, dict) or entry.get("api_key") != "${" + name + "}":
            raise ConfigError("api_key 必须引用指定环境变量，不能写入明文密钥。")
    try:
        config = Settings.model_validate(_expand(raw, environment))
    except ValidationError:
        raise ConfigError("配置字段或取值不合法，请对照 config.yaml 示例检查。") from None
    for field in ("output_dir", "log_dir", "database_path"):
        value = getattr(config.app, field)
        setattr(config.app, field, (path.parent / value).resolve())
    missing = config.missing_secrets()
    if require_secrets and missing:
        raise ConfigError("请在本地 .env 填写：" + "、".join(missing))
    return config
