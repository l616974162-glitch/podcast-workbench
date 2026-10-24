import asyncio
import io
import struct
import wave
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_llm import ROOT, context
from test_store import example_script

from podcast_workbench.audio import AudioRenderer, split_text
from podcast_workbench.config import load_config
from podcast_workbench.errors import ServiceError
from podcast_workbench.models import ProgramSettings, Revision, Turn
from podcast_workbench.tools.registry import create_tts_client


def wav_bytes(sample: int = 100, frames: int = 240) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(struct.pack("<h", sample) * frames)
    return output.getvalue()


def test_split_keeps_all_text_and_provider_limit() -> None:
    text = "这是一句话。" * 200
    parts = split_text(text, 600)
    assert "".join(parts) == text
    assert all(0 < len(part) <= 600 for part in parts)


def test_real_ffmpeg_normalization_order_and_cache(tmp_path: Path) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            import json

            data = json.loads(request.content)
            calls.append(data)
            assert data["input"]["language_type"] == "Chinese"
            assert request.headers.get("Authorization", "").startswith("Bearer ")
            return httpx.Response(
                200,
                json={
                    "output": {
                        "audio": {
                            "url": "https://result.oss-cn-beijing.aliyuncs.com/"
                            + str(len(calls))
                            + ".wav"
                        }
                    }
                },
            )
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=wav_bytes(100 if request.url.path == "/1.wav" else 200))

    async def run() -> None:
        config = load_config(ROOT / "config.yaml")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            renderer = AudioRenderer(
                config, create_tts_client(config.tts, http), tmp_path / "output"
            )
            revision = Revision(
                session_id=uuid4(), number=1, script=example_script(), settings=ProgramSettings()
            )
            first = await renderer.render(revision, revision.settings, context(tmp_path))
            with wave.open(str(first.path), "rb") as wav:
                samples = struct.unpack(
                    "<" + "h" * wav.getnframes(), wav.readframes(wav.getnframes())
                )
            assert samples[:240] == (100,) * 240 and samples[240:] == (200,) * 240
            assert first.duration_seconds == pytest.approx(0.02)
            await renderer.render(revision, revision.settings, context(tmp_path))
            assert len(calls) == 2
            changed = revision.model_copy(
                update={
                    "script": revision.script.model_copy(
                        update={
                            "turns": [
                                revision.script.turns[0],
                                Turn(role="guest", text="修改后的回答"),
                            ]
                        }
                    )
                }
            )
            await renderer.render(changed, changed.settings, context(tmp_path))
            assert len(calls) == 3
            await renderer.render(changed, ProgramSettings(host_voice="Serena"), context(tmp_path))
            assert len(calls) == 4

    asyncio.run(run())


@pytest.mark.parametrize("length_header", [True, False])
def test_download_limit_checked_with_or_without_content_length(
    tmp_path: Path,
    length_header: bool,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {
                        "audio": {
                            "url": "https://result.oss-cn-beijing.aliyuncs.com/test.wav?Signature=secret"
                        }
                    }
                },
            )
        headers = {"Content-Length": "1000"} if length_header else {"Transfer-Encoding": "chunked"}
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(b"x" * 1000))

    async def run() -> None:
        config = load_config(ROOT / "config.yaml").tts.model_copy(update={"max_audio_bytes": 100})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            await create_tts_client(config, http).execute(
                "测试",
                "Cherry",
                tmp_path / "output.wav",
                uuid4(),
                ProgramSettings(),
                context(tmp_path),
            )

    with pytest.raises(ServiceError) as error:
        asyncio.run(run())
    assert "Signature" not in str(error.value)
    assert not (tmp_path / "output.wav").exists()
    assert not (tmp_path / "output.wav.part").exists()


def test_download_rejects_untrusted_url_and_does_not_follow_redirect(tmp_path: Path) -> None:
    gets = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200, json={"output": {"audio": {"url": "http://127.0.0.1/private"}}}
            )
        gets.append(request)
        return httpx.Response(200, content=wav_bytes())

    async def run() -> None:
        config = load_config(ROOT / "config.yaml").tts
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            await create_tts_client(config, http).execute(
                "测试",
                "Cherry",
                tmp_path / "out.wav",
                uuid4(),
                ProgramSettings(),
                context(tmp_path),
            )

    with pytest.raises(ServiceError):
        asyncio.run(run())
    assert gets == []


def test_missing_segment_prevents_final_program(tmp_path: Path) -> None:
    posts = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=wav_bytes())
        posts.append(request)
        if len(posts) == 2:
            return httpx.Response(502, text="private provider error")
        return httpx.Response(
            200,
            json={
                "output": {"audio": {"url": "https://result.oss-cn-beijing.aliyuncs.com/one.wav"}}
            },
        )

    async def run() -> None:
        config = load_config(ROOT / "config.yaml")
        revision = Revision(
            session_id=uuid4(), number=1, script=example_script(), settings=ProgramSettings()
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            await AudioRenderer(
                config, create_tts_client(config.tts, http), tmp_path / "output"
            ).render(
                revision,
                revision.settings,
                context(tmp_path),
            )

    with pytest.raises(ServiceError):
        asyncio.run(run())
    assert not any(path.parent.name != ".cache" for path in (tmp_path / "output").glob("*/*.wav"))


@pytest.mark.parametrize("mode", ["redirect", "truncated"])
def test_redirect_or_truncated_audio_is_never_published(tmp_path: Path, mode: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {
                        "audio": {"url": "https://result.oss-cn-beijing.aliyuncs.com/file.wav"}
                    }
                },
            )
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})
        return httpx.Response(200, content=wav_bytes()[:-100])

    async def run():
        config = load_config(ROOT / "config.yaml")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            await create_tts_client(config.tts, http).execute(
                "测试",
                "Cherry",
                tmp_path / "out.wav",
                uuid4(),
                ProgramSettings(),
                context(tmp_path),
            )

    with pytest.raises(ServiceError):
        asyncio.run(run())
    assert not (tmp_path / "out.wav").exists()


@pytest.mark.parametrize("payload", [{}, {"output": {"audio": {}}}])
def test_missing_audio_url_is_safe(tmp_path: Path, payload: dict) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, json=payload)

    async def run():
        config = load_config(ROOT / "config.yaml")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            await create_tts_client(config.tts, http).execute(
                "测试",
                "Cherry",
                tmp_path / "out.wav",
                uuid4(),
                ProgramSettings(),
                context(tmp_path),
            )

    with pytest.raises(ServiceError):
        asyncio.run(run())
    assert not (tmp_path / "out.wav").exists()
