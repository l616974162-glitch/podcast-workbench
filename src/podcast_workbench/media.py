"""音频有效性与 FFmpeg 定位。"""

import importlib
import os
import shutil
import subprocess
import wave
from pathlib import Path

from podcast_workbench.errors import ServiceError


def resolve_ffmpeg(configured: str = "") -> str:
    if configured:
        found = shutil.which(configured)
        if not found:
            raise ServiceError("audio_error")
        return found
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        found = str(importlib.import_module("imageio_ffmpeg").get_ffmpeg_exe())
        if Path(found).is_file():
            return found
    except (ImportError, RuntimeError, OSError):
        pass
    raise ServiceError("audio_error")


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            if frames <= 0 or wav.getframerate() <= 0:
                raise ServiceError("audio_error")
            expected = frames * wav.getnchannels() * wav.getsampwidth()
            actual = 0
            while data := wav.readframes(8192):
                actual += len(data)
            if actual != expected:
                raise ServiceError("audio_error")
            return frames / wav.getframerate()
    except (OSError, EOFError, wave.Error):
        raise ServiceError("audio_error") from None


def normalize_audio(source: Path, target: Path, ffmpeg_path: str, max_bytes: int) -> None:
    """将供应商音频统一为单声道 24kHz PCM WAV，并原子发布。"""
    temporary = target.with_suffix(target.suffix + ".normalize.part")
    try:
        subprocess.run(
            [
                resolve_ffmpeg(ffmpeg_path),
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
        if temporary.stat().st_size > max_bytes:
            raise ServiceError("audio_error")
        temporary.replace(target)
    except (OSError, subprocess.SubprocessError):
        raise ServiceError("audio_error") from None
    finally:
        temporary.unlink(missing_ok=True)
