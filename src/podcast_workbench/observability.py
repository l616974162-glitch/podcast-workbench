"""仅记录受控结构字段，不保存供应商响应、异常正文或请求材料。"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from podcast_workbench.models import ErrorCode, Stage, utc_now


class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True, allow_inf_nan=False)

    timestamp: datetime
    event: Literal[
        "run.start", "run.end", "run.error", "node.start", "node.end", "node.error",
        "llm.start", "llm.end", "llm.error", "tool.start", "tool.end", "tool.error",
        "audio_merge.start", "audio_merge.end", "audio_merge.error",
        "cache.hit",
    ]
    trace_id: UUID
    session_id: UUID
    job_id: UUID | None = None
    revision_id: UUID | None = None
    node: Literal["researcher", "writer", "reviewer", "tts", "audio_merge"] | None = None
    provider: Literal["deepseek", "qwen", "edge"] | None = None
    status: Literal["queued", "running", "succeeded", "failed"] = "running"
    duration_ms: float | None = Field(default=None, ge=0)
    error_code: ErrorCode | None = None


class EventLogger:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._lock = Lock()

    def emit(self, fields: dict[str, object]) -> None:
        try:
            record = _Record.model_validate({**fields, "timestamp": utc_now()})
        except ValidationError:
            raise ValueError("日志字段不合法，原始内容未记录。") from None
        line = record.model_dump_json(exclude_none=True) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line)


@dataclass
class RunContext:
    trace_id: UUID
    session_id: UUID
    job_id: UUID
    logger: EventLogger
    revision_id: UUID | None = None
    on_stage: Callable[[Stage], None] | None = None

    def emit(self, event: str, **fields: object) -> None:
        self.logger.emit({
            **fields, "event": event, "trace_id": self.trace_id,
            "session_id": self.session_id, "job_id": self.job_id,
            "revision_id": self.revision_id,
        })

    def stage(self, stage: Stage) -> None:
        if self.on_stage is not None:
            self.on_stage(stage)
