import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from test_llm import ROOT
from test_scripts import CapturingGraph
from test_store import example_script

from podcast_workbench.config import ConfigError, load_config
from podcast_workbench.errors import ServiceError
from podcast_workbench.models import Artifact, ChatRequest, ProgramSettings
from podcast_workbench.observability import EventLogger
from podcast_workbench.scripts import ScriptWorkflow
from podcast_workbench.service import WorkbenchService
from podcast_workbench.store import BusySessionError, Store


class RecordingAudio:
    def __init__(self, directory: Path):
        self.directory = directory
        self.calls = []
        self.fail = False

    async def render(self, revision, settings, ctx):
        from test_audio import wav_bytes

        self.calls.append((revision, settings))
        if self.fail:
            raise ServiceError("audio_error")
        path = self.directory / (str(uuid4()) + ".wav")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav_bytes())
        return Artifact(
            revision_id=revision.id,
            kind="audio",
            path=path,
            mime_type="audio/wav",
            duration_seconds=0.01,
            audio_settings=settings,
        )


def make_service(tmp_path: Path):
    config = load_config(ROOT / "config.yaml")
    config.llm.api_key = SecretStr("test-only-llm")
    config.tts.api_key = SecretStr("test-only-tts")
    config.app.output_dir = tmp_path / "output"
    config.app.database_path = tmp_path / "data" / "workbench.db"
    config.app.log_dir = tmp_path / "logs"
    store = Store(config.app.database_path)
    graph = CapturingGraph()
    audio = RecordingAudio(config.app.output_dir)
    service = WorkbenchService(
        config,
        store,
        ScriptWorkflow(store, graph, config.app.output_dir),
        audio,
        EventLogger(config.app.log_dir / "agent.log"),
    )
    return service, graph, audio


def test_preview_and_voice_only_render_do_not_rewrite_script(tmp_path: Path) -> None:
    service, graph, audio = make_service(tmp_path)

    async def run():
        first = service.submit(ChatRequest(action="create", source_text="原文", preview_only=True))
        await service.drain()
        assert service.store.get_job(first.id).status == "succeeded" and len(audio.calls) == 0
        revision_id = service.store.get_session(first.session_id).current_revision.id
        second = service.submit(
            ChatRequest(
                action="render_audio",
                session_id=first.session_id,
                settings=ProgramSettings(host_voice="Serena"),
            )
        )
        await service.drain()
        assert service.store.get_job(second.id).status == "succeeded"
        assert service.store.get_session(first.session_id).current_revision.id == revision_id
        assert len(graph.inputs) == 1
        assert audio.calls[-1][1].host_voice == "Serena"

    asyncio.run(run())


def test_failed_audio_keeps_new_script_and_old_successful_program(tmp_path: Path) -> None:
    service, _, audio = make_service(tmp_path)

    async def run():
        first = service.submit(ChatRequest(action="create", source_text="原文"))
        await service.drain()
        previous = service.store.get_session(first.session_id).published_audio.id
        audio.fail = True
        revised = service.submit(
            ChatRequest(action="revise", session_id=first.session_id, message="缩短节目")
        )
        await service.drain()
        session = service.store.get_session(first.session_id)
        assert service.store.get_job(revised.id).status == "failed"
        assert session.current_revision.number == 2 and session.published_audio.id == previous
        assert session.audio_is_stale
        assert session.active_job is None

    asyncio.run(run())


def test_duplicate_job_is_rejected_and_no_extra_revision_created(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)

    async def run():
        session = service.store.create_session("原文", ProgramSettings())
        service.store.save_revision(session, example_script())
        request = ChatRequest(
            action="revise", session_id=session, message="修改", preview_only=True
        )
        service.submit(request)
        with pytest.raises(BusySessionError):
            service.submit(request)
        await service.drain()
        assert service.store.get_session(session).current_revision.number == 2

    asyncio.run(run())


def test_missing_keys_rejects_before_creating_a_job(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    service.config.llm.api_key = SecretStr("")
    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        service.submit(ChatRequest(action="create", source_text="原文", preview_only=True))


def test_failed_initial_script_can_be_retried_in_same_session(tmp_path: Path) -> None:
    service, graph, _ = make_service(tmp_path)

    async def run():
        graph.fail = True
        first = service.submit(ChatRequest(action="create", source_text="原文", preview_only=True))
        await service.drain()
        assert service.store.get_job(first.id).status == "failed"
        graph.fail = False
        retry = service.submit(
            ChatRequest(
                action="revise", session_id=first.session_id, message="重新生成", preview_only=True
            )
        )
        await service.drain()
        assert service.store.get_job(retry.id).status == "succeeded"
        assert service.store.get_session(first.session_id).current_revision.number == 1

    asyncio.run(run())


def test_voice_choice_survives_future_renders_and_script_edits(tmp_path: Path) -> None:
    service, _, audio = make_service(tmp_path)

    async def run():
        first = service.submit(ChatRequest(action="create", source_text="原文"))
        await service.drain()
        service.submit(
            ChatRequest(
                action="render_audio",
                session_id=first.session_id,
                settings=ProgramSettings(host_voice="Serena"),
            )
        )
        await service.drain()
        assert service.store.get_session(first.session_id).settings.host_voice == "Serena"
        service.submit(ChatRequest(action="revise", session_id=first.session_id, message="更通俗"))
        await service.drain()
        assert audio.calls[-1][1].host_voice == "Serena"

    asyncio.run(run())
