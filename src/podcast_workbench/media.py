"""音频有效性与 FFmpeg 定位。"""

import importlib
import shutil
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
