"""工具客户端模板：子类只覆盖请求构造和响应解析。"""

from abc import ABC, abstractmethod
from pathlib import Path
from time import perf_counter
from typing import Protocol
from uuid import UUID

import httpx

from podcast_workbench.config import TTSConfig
from podcast_workbench.errors import ServiceError
from podcast_workbench.models import Artifact, ProgramSettings
from podcast_workbench.observability import RunContext


class SpeechClient(Protocol):
    async def execute(
        self,
        text: str,
        voice: str,
        destination: Path,
        revision_id: UUID,
        settings: ProgramSettings,
        ctx: RunContext,
    ) -> Artifact: ...


class ToolClient(ABC):
    def __init__(self, config: TTSConfig, http: httpx.AsyncClient) -> None:
        self.config = config
        self.http = http

    async def execute(
        self,
        text: str,
        voice: str,
        destination: Path,
        revision_id: UUID,
        settings: ProgramSettings,
        ctx: RunContext,
    ) -> Artifact:
        if not text.strip() or len(text) > self.config.max_text_chars:
            raise ServiceError("audio_error")
        started = perf_counter()
        ctx.emit("tool.start", node="tts", provider="qwen")
        try:
            response = await self.http.post(
                str(self.config.endpoint),
                json=self._build_request(text, voice),
                headers={"Authorization": "Bearer " + self.config.api_key.get_secret_value()},
                timeout=self.config.timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
            artifact = await self._parse_response(response, destination, revision_id, settings)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError, ServiceError):
            ctx.emit(
                "tool.error", node="tts", provider="qwen", status="failed", error_code="audio_error"
            )
            raise ServiceError("audio_error") from None
        ctx.emit(
            "tool.end",
            node="tts",
            provider="qwen",
            status="succeeded",
            duration_ms=(perf_counter() - started) * 1000,
        )
        return artifact

    @abstractmethod
    def _build_request(self, text: str, voice: str) -> dict[str, object]: ...

    @abstractmethod
    async def _parse_response(
        self,
        response: httpx.Response,
        destination: Path,
        revision_id: UUID,
        settings: ProgramSettings,
    ) -> Artifact: ...
