"""业务输入和持久化快照；不调用外部服务。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

Action = Literal["create", "revise", "render_audio"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]
Stage = Literal["queued", "researcher", "writer", "reviewer", "tts", "merge", "done", "failed"]
ErrorCode = Literal[
    "upstream_error", "invalid_output", "audio_error", "interrupted", "internal_error",
]
ERROR_MESSAGES: dict[ErrorCode, str] = {
    "upstream_error": "外部服务调用失败，请检查服务配置或稍后重试。",
    "invalid_output": "生成内容未通过结构校验，请重试。",
    "audio_error": "音频制作失败，已有脚本和成品已保留。",
    "interrupted": "服务重启导致任务中断，请重新发起。",
    "internal_error": "内部处理失败，已有结果已保留，请重试或检查本地环境。",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True, frozen=True,
    )


class ProgramSettings(Model):
    topic: str = Field(default="", max_length=120)
    audience: Literal["general", "beginner", "professional"] = "general"
    style: Literal["casual", "educational"] = "casual"
    target_minutes: Literal[2, 3, 5] = 3
    host_voice: str = Field(default="Cherry", min_length=1, max_length=80)
    guest_voice: str = Field(default="Ethan", min_length=1, max_length=80)


class ChatRequest(Model):
    action: Action
    session_id: UUID | None = None
    source_text: str | None = Field(default=None, min_length=1, max_length=20000)
    message: str = Field(default="", max_length=4000)
    settings: ProgramSettings | None = None
    preview_only: bool = False

    @model_validator(mode="after")
    def check_action(self) -> Self:
        if self.action == "create":
            if self.source_text is None:
                raise ValueError("创建节目必须提供文本材料。")
            if self.session_id is not None:
                raise ValueError("创建节目不能覆盖已有会话，请新建或使用修改操作。")
        else:
            if self.session_id is None:
                raise ValueError("修改或生成音频必须指定会话。")
            if self.source_text is not None:
                raise ValueError("后续操作不能替换原材料，请另建节目。")
            if self.action == "revise" and not self.message:
                raise ValueError("修改脚本必须提供修改要求。")
        return self


class Turn(Model):
    role: Literal["host", "guest"]
    text: str = Field(min_length=1, max_length=4000)


class Analysis(Model):
    key_points: list[str] = Field(min_length=1)
    outline: list[str] = Field(min_length=1)
    information_gaps: list[str] = Field(default_factory=list)


class Script(Model):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    turns: list[Turn] = Field(min_length=2, max_length=200)
    outline: list[str] = Field(default_factory=list)
    review_summary: str = ""

    @model_validator(mode="after")
    def check_roles(self) -> Self:
        if {turn.role for turn in self.turns} != {"host", "guest"}:
            raise ValueError("双人脚本必须同时包含主持人与嘉宾。")
        return self


class Revision(Model):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    number: int = Field(gt=0)
    script: Script
    settings: ProgramSettings
    created_at: datetime = Field(default_factory=utc_now)


class Message(Model):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)
    created_at: datetime = Field(default_factory=utc_now)


class Artifact(Model):
    id: UUID = Field(default_factory=uuid4)
    revision_id: UUID
    job_id: UUID | None = None
    kind: Literal["audio", "script"]
    path: Path
    mime_type: Literal["audio/wav", "audio/mpeg", "text/plain"]
    duration_seconds: float | None = Field(default=None, gt=0)
    audio_settings: ProgramSettings | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def check_media(self) -> Self:
        if self.kind == "audio":
            if (not self.mime_type.startswith("audio/") or self.duration_seconds is None
                    or self.audio_settings is None):
                raise ValueError("音频产物必须记录音频类型、有效时长和实际音色设置。")
        elif (self.mime_type != "text/plain" or self.duration_seconds is not None
              or self.audio_settings is not None):
            raise ValueError("脚本产物必须是无音频时长的文本。")
        if not self.path.is_absolute():
            raise ValueError("产物索引必须使用服务端生成的绝对路径。")
        return self


class Job(Model):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    trace_id: UUID
    action: Action
    revision_id: UUID | None = None
    status: JobStatus = "queued"
    stage: Stage = "queued"
    error_code: ErrorCode | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_message(self) -> str | None:
        return ERROR_MESSAGES[self.error_code] if self.error_code else None


class Session(Model):
    id: UUID
    source_text: str
    settings: ProgramSettings
    current_revision: Revision | None = None
    published_audio: Artifact | None = None
    active_job: Job | None = None
    messages: list[Message] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def audio_is_stale(self) -> bool:
        return bool(self.published_audio and self.current_revision
                    and self.published_audio.revision_id != self.current_revision.id)
