"""分角色语音、片段缓存、格式统一与按序拼接。"""

import asyncio
import hashlib
import json
import os
import subprocess
import wave
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from podcast_workbench.config import Settings
from podcast_workbench.errors import ServiceError
from podcast_workbench.media import resolve_ffmpeg, wav_duration
from podcast_workbench.models import Artifact, ProgramSettings, Revision
from podcast_workbench.observability import RunContext
from podcast_workbench.tools.base import ToolClient


def split_text(text: str, limit: int) -> list[str]:
    parts = []
    while text:
        cut = min(len(text), limit)
        if len(text) > limit:
            boundary = max(text.rfind(mark, 0, limit) for mark in "。！？；\n.!?;")
            if boundary >= limit // 2:
                cut = boundary + 1
        parts.append(text[:cut])
        text = text[cut:]
    return parts


class AudioRenderer:
    def __init__(self, config: Settings, client: ToolClient, output_dir: Path) -> None:
        self.config = config
        self.client = client
        self.output_dir = output_dir.resolve()
        self._cache_lock = asyncio.Lock()

    def _normalize(self, source: Path, target: Path) -> None:
        temporary = target.with_suffix(".part")
        try:
            subprocess.run(
                [
                    resolve_ffmpeg(self.config.app.ffmpeg_path),
                    "-nostdin",
                    "-v",
                    "error",
                    "-y",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(source),
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-c:a",
                    "pcm_s16le",
                    "-f",
                    "wav",
                    str(temporary),
                ],
                check=True,
                timeout=60,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            wav_duration(temporary)
            if temporary.stat().st_size > self.config.tts.max_audio_bytes:
                raise ServiceError("audio_error")
            temporary.replace(target)
        except (OSError, subprocess.SubprocessError):
            raise ServiceError("audio_error") from None
        finally:
            temporary.unlink(missing_ok=True)

    def _merge(self, paths: list[Path], target: Path) -> float:
        temporary = target.with_suffix(".part")
        target.parent.mkdir(parents=True, exist_ok=True)
        total = 44
        try:
            with wave.open(str(temporary), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                for path in paths:
                    with wave.open(str(path), "rb") as part:
                        if (part.getnchannels(), part.getsampwidth(), part.getframerate()) != (
                            1,
                            2,
                            24000,
                        ):
                            raise ServiceError("audio_error")
                        while data := part.readframes(8192):
                            total += len(data)
                            if total > self.config.tts.max_audio_bytes:
                                raise ServiceError("audio_error")
                            output.writeframesraw(data)
            duration = wav_duration(temporary)
            temporary.replace(target)
            return duration
        except (OSError, EOFError, wave.Error):
            raise ServiceError("audio_error") from None
        finally:
            temporary.unlink(missing_ok=True)

    async def render(
        self,
        revision: Revision,
        settings: ProgramSettings,
        ctx: RunContext,
    ) -> Artifact:
        resolve_ffmpeg(self.config.app.ffmpeg_path)
        if settings.host_voice == settings.guest_voice:
            raise ServiceError("audio_error")
        segments = [
            (turn.role, text)
            for turn in revision.script.turns
            for text in split_text(turn.text, self.config.tts.max_text_chars)
            if text.strip()
        ]
        if len(segments) > self.config.tts.max_segments:
            raise ServiceError("audio_error")
        ctx.stage("tts")
        cache = self.output_dir / ".cache"
        cache.mkdir(parents=True, exist_ok=True)
        paths = []
        for role, text in segments:
            voice = settings.host_voice if role == "host" else settings.guest_voice
            fingerprint = json.dumps(
                {
                    "text": text,
                    "role": role,
                    "voice": voice,
                    "model": self.config.tts.model,
                    "language": self.config.tts.language_type,
                    "endpoint": str(self.config.tts.endpoint),
                    "format": "pcm_s16le_24000_v1",
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
            key = hashlib.sha256(fingerprint).hexdigest()
            path = cache / (key + ".wav")
            async with self._cache_lock:
                valid = False
                if path.exists():
                    try:
                        wav_duration(path)
                        valid = path.stat().st_size <= self.config.tts.max_audio_bytes
                    except ServiceError:
                        valid = False
                if valid:
                    ctx.emit("cache.hit", node="tts", status="succeeded")
                else:
                    raw = cache / (key + ".source.wav")
                    try:
                        await self.client.execute(text, voice, raw, revision.id, settings, ctx)
                        await asyncio.to_thread(self._normalize, raw, path)
                    finally:
                        raw.unlink(missing_ok=True)
            paths.append(path)
        ctx.stage("merge")
        ctx.emit("audio_merge.start", node="audio_merge")
        started = perf_counter()
        target = self.output_dir / str(revision.id) / (str(uuid4()) + ".wav")
        try:
            duration = await asyncio.to_thread(self._merge, paths, target)
        except ServiceError:
            ctx.emit(
                "audio_merge.error", node="audio_merge", status="failed", error_code="audio_error"
            )
            raise
        ctx.emit(
            "audio_merge.end",
            node="audio_merge",
            status="succeeded",
            duration_ms=(perf_counter() - started) * 1000,
        )
        return Artifact(
            revision_id=revision.id,
            kind="audio",
            path=target,
            mime_type="audio/wav",
            duration_seconds=duration,
            audio_settings=settings,
        )
