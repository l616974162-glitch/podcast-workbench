import asyncio
import subprocess
from pathlib import Path
from uuid import uuid4

import edge_tts
import httpx
import pytest
from test_audio import wav_bytes
from test_llm import ROOT, context
from test_service import make_service
from test_store import example_script

from podcast_workbench.config import ConfigError, load_config
from podcast_workbench.errors import ServiceError
from podcast_workbench.media import resolve_ffmpeg
from podcast_workbench.models import ChatRequest, ProgramSettings
from podcast_workbench.tools.edge_tts import EdgeTTSClient
from podcast_workbench.tools.registry import create_tts_client


def edge_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TTS_PROTOCOL", "edge_tts")
    return load_config(ROOT / "config.yaml", env_file=tmp_path / "absent.env")


def test_new_install_defaults_to_edge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TTS_PROTOCOL", raising=False)
    config = load_config(ROOT / "config.yaml", env_file=tmp_path / "absent.env")
    assert config.tts.protocol == "edge_tts"
    assert not config.tts.needs_api_key


def test_edge_only_requires_deepseek_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_PROTOCOL", "edge_tts")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    config = load_config(
        ROOT / "config.yaml", env_file=tmp_path / "absent.env", require_secrets=True
    )
    assert config.missing_secrets() == []
    assert config.tts.default_voices() == ("zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural")
    monkeypatch.setenv("TTS_PROTOCOL", "qwen_tts")
    with pytest.raises(ConfigError, match="DASHSCOPE_API_KEY"):
        load_config(ROOT / "config.yaml", env_file=tmp_path / "absent.env", require_secrets=True)


def test_edge_stream_converts_mp3_to_real_wav(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = edge_config(tmp_path, monkeypatch)
    encoded = subprocess.run(
        [
            resolve_ffmpeg(),
            "-nostdin",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "mp3",
            "pipe:1",
        ],
        input=wav_bytes(),
        capture_output=True,
        check=True,
        timeout=20,
    ).stdout
    calls = []

    class Communicate:
        def __init__(self, text, voice, **kwargs):
            calls.append((text, voice, kwargs))

        async def stream(self):
            yield {"type": "WordBoundary", "text": "测试"}
            yield {"type": "audio", "data": encoded}

    monkeypatch.setattr(edge_tts, "Communicate", Communicate)

    async def run():
        async with httpx.AsyncClient() as http:
            client = create_tts_client(config.tts, http)
            assert isinstance(client, EdgeTTSClient)
            settings = ProgramSettings(
                host_voice="zh-CN-XiaoxiaoNeural", guest_voice="zh-CN-YunxiNeural"
            )
            return await client.execute(
                "测试",
                settings.host_voice,
                tmp_path / "part.wav",
                uuid4(),
                settings,
                context(tmp_path),
            )

    artifact = asyncio.run(run())
    assert artifact.path.read_bytes().startswith(b"RIFF")
    assert artifact.duration_seconds > 0 and artifact.kind == "audio"
    assert calls[0][1] == "zh-CN-XiaoxiaoNeural"
    assert "api_key" not in calls[0][2]
    assert '"provider":"edge"' in (tmp_path / "agent.log").read_text()
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("mode", ["oversize", "empty", "timeout"])
def test_edge_failure_is_safe_and_cleans_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    config = edge_config(tmp_path, monkeypatch).tts.model_copy(
        update={
            "max_audio_bytes": 100,
            "timeout_seconds": 0.02,
        }
    )

    class Communicate:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self):
            if mode == "timeout":
                await asyncio.sleep(0.1)
            if mode != "empty":
                yield {"type": "audio", "data": b"x" * 101}

    monkeypatch.setattr(edge_tts, "Communicate", Communicate)

    async def run():
        async with httpx.AsyncClient() as http:
            await EdgeTTSClient(config, http).execute(
                "测试",
                "zh-CN-XiaoxiaoNeural",
                tmp_path / "part.wav",
                uuid4(),
                ProgramSettings(),
                context(tmp_path),
            )

    with pytest.raises(ServiceError) as error:
        asyncio.run(run())
    assert error.value.code == "audio_error"
    assert not (tmp_path / "part.wav").exists()
    assert not list(tmp_path.glob("*.part"))


def test_voice_only_edge_job_needs_neither_supplier_key(tmp_path: Path) -> None:
    from pydantic import SecretStr

    service, graph, audio = make_service(tmp_path)
    service.config.tts.protocol = "edge_tts"
    service.config.tts.api_key = SecretStr("")
    service.config.llm.api_key = SecretStr("")
    selected = ProgramSettings(host_voice="zh-CN-XiaoxiaoNeural", guest_voice="zh-CN-YunxiNeural")
    session = service.store.create_session("原文", selected)
    service.store.save_revision(session, example_script())

    async def run():
        job = service.submit(ChatRequest(action="render_audio", session_id=session))
        await service.drain()
        assert service.store.get_job(job.id).status == "succeeded"

    asyncio.run(run())
    assert graph.inputs == [] and len(audio.calls) == 1


def test_cache_never_crosses_provider_boundary(tmp_path: Path) -> None:
    from podcast_workbench.audio import AudioRenderer
    from podcast_workbench.models import Artifact, Revision

    config = load_config(ROOT / "config.yaml")
    calls = []

    class Client:
        async def execute(self, text, voice, destination, revision_id, settings, ctx):
            calls.append(text)
            destination.write_bytes(wav_bytes())
            return Artifact(
                revision_id=revision_id,
                kind="audio",
                path=destination,
                mime_type="audio/wav",
                duration_seconds=0.01,
                audio_settings=settings,
            )

    async def run():
        selected = ProgramSettings()
        revision = Revision(
            session_id=uuid4(), number=1, script=example_script(), settings=selected
        )
        await AudioRenderer(config, Client(), tmp_path / "output").render(
            revision,
            selected,
            context(tmp_path),
        )
        assert len(calls) == 2
        config.tts.protocol = "edge_tts"
        await AudioRenderer(config, Client(), tmp_path / "output").render(
            revision,
            selected,
            context(tmp_path),
        )
        assert len(calls) == 4

    asyncio.run(run())
