"""通过 edge-tts 的公开库接口调用 Edge 在线语音，不需要 API Key。"""

import asyncio
from pathlib import Path
from time import perf_counter
from uuid import UUID

import edge_tts
import httpx

from podcast_workbench.config import TTSConfig
from podcast_workbench.errors import ServiceError
from podcast_workbench.media import normalize_audio, wav_duration
from podcast_workbench.models import Artifact, ProgramSettings
from podcast_workbench.observability import RunContext


class EdgeTTSClient:
    def __init__(self, config: TTSConfig, http: httpx.AsyncClient, ffmpeg_path: str = "") -> None:
        self.config = config
        self.ffmpeg_path = ffmpeg_path

    async def execute(
        self,
        text: str,
        voice: str,
        destination: Path,
        revision_id: UUID,
        settings: ProgramSettings,
        ctx: RunContext,
    ) -> Artifact:
        if (
            not text.strip()
            or len(text) > self.config.max_text_chars
            or not voice.endswith("Neural")
        ):
            raise ServiceError("audio_error")
        started = perf_counter()
        ctx.emit("tool.start", node="tts", provider="edge")
        raw = destination.with_suffix(destination.suffix + ".mp3.part")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            communicate = edge_tts.Communicate(
                text,
                voice,
                connect_timeout=max(1, int(self.config.timeout_seconds)),
                receive_timeout=max(1, int(self.config.timeout_seconds)),
            )
            total = 0
            async with asyncio.timeout(self.config.timeout_seconds):
                with raw.open("wb") as output:
                    async for chunk in communicate.stream():
                        if chunk["type"] != "audio":
                            continue
                        data = chunk["data"]
                        total += len(data)
                        if total > self.config.max_audio_bytes:
                            raise ServiceError("audio_error")
                        output.write(data)
            if total == 0:
                raise ServiceError("audio_error")
            await asyncio.to_thread(
                normalize_audio,
                raw,
                destination,
                self.ffmpeg_path,
                self.config.max_audio_bytes,
            )
            duration = wav_duration(destination)
        except Exception:
            ctx.emit(
                "tool.error", node="tts", provider="edge", status="failed", error_code="audio_error"
            )
            raise ServiceError("audio_error") from None
        finally:
            raw.unlink(missing_ok=True)
        ctx.emit(
            "tool.end",
            node="tts",
            provider="edge",
            status="succeeded",
            duration_ms=(perf_counter() - started) * 1000,
        )
        return Artifact(
            revision_id=revision_id,
            kind="audio",
            path=destination.resolve(),
            mime_type="audio/wav",
            duration_seconds=duration,
            audio_settings=settings,
            tts_protocol="edge_tts",
        )
