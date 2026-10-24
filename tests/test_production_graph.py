import asyncio
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from test_covers import png, setup_cover
from test_service import make_service

from podcast_workbench.errors import ServiceError
from podcast_workbench.models import ChatRequest


@pytest.mark.parametrize("broken", [None, "audio", "image", "missing_key"])
def test_one_request_coordinates_all_agents_and_preserves_partial_results(
    tmp_path: Path, broken: str | None,
) -> None:
    import base64

    def handler(request):
        if broken == "image":
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})

    service, graph, audio, planner, http, _, _ = setup_cover(tmp_path, handler)
    service.config.pipeline.auto_cover = True
    audio.fail = broken == "audio"
    if broken == "missing_key":
        service.config.image.api_key = SecretStr("")

    async def run():
        job = service.submit(ChatRequest(action="create", source_text="自动制作的材料"))
        await service.drain()
        result = service.store.get_job(job.id)
        session = service.store.get_session(job.session_id)
        assert len(graph.inputs) == 1 and session.current_revision is not None
        assert len(audio.calls) == 1
        assert len(planner.calls) == (0 if broken == "missing_key" else 1)
        assert bool(session.published_audio) == (broken != "audio")
        assert bool(session.published_cover) == (broken not in {"image", "missing_key"})
        assert result.status == ("succeeded" if broken is None else "failed")
        assert result.partial == (broken is not None)
        assert result.node_states["tts"] == ("failed" if broken == "audio" else "succeeded")
        assert result.node_states["image"] == (
            "failed" if broken in {"image", "missing_key"} else "succeeded"
        )
        if broken == "missing_key":
            assert "AGNES_API_KEY" in "".join(result.warnings)
        artifacts = service.store.list_artifacts(job.session_id)
        assert all(item.job_id == job.id for item in artifacts)
        await http.aclose()

    asyncio.run(run())


def test_preview_and_opt_out_do_not_call_cover_team(tmp_path: Path) -> None:
    service, graph, audio = make_service(tmp_path)
    service.config.pipeline.auto_cover = True

    async def run():
        first = service.submit(ChatRequest(
            action="create", source_text="只看脚本", preview_only=True,
        ))
        await service.drain()
        assert service.store.get_job(first.id).status == "succeeded"
        second = service.submit(ChatRequest(
            action="create", source_text="只做音频", auto_cover=False,
        ))
        await service.drain()
        assert service.store.get_job(second.id).status == "succeeded"
        assert len(graph.inputs) == 2 and len(audio.calls) == 1
    asyncio.run(run())


def test_script_failure_never_starts_production_branches(tmp_path: Path) -> None:
    service, graph, audio = make_service(tmp_path)
    service.config.pipeline.auto_cover = True
    graph.fail = True

    async def run():
        job = service.submit(ChatRequest(action="create", source_text="失败的脚本"))
        await service.drain()
        assert service.store.get_job(job.id).status == "failed"
        assert service.store.get_session(job.session_id).current_revision is None
        assert audio.calls == []
    asyncio.run(run())


def test_parallel_cover_planning_does_not_wait_for_audio(tmp_path: Path) -> None:
    import base64
    image_started = asyncio.Event()

    def handler(request):
        image_started.set()
        return httpx.Response(200, json={"data": [{
            "b64_json": base64.b64encode(png()).decode(),
        }]})
    service, _, audio, planner, http, _, _ = setup_cover(
        tmp_path, handler,
    )
    service.config.pipeline.auto_cover = True
    original_render = audio.render

    async def render(revision, settings, ctx):
        try:
            await asyncio.wait_for(image_started.wait(), timeout=5)
        except TimeoutError:
            raise ServiceError("audio_error") from None
        return await original_render(revision, settings, ctx)
    audio.render = render

    async def run():
        job = service.submit(ChatRequest(action="create", source_text="并行制作"))
        await service.drain()
        assert service.store.get_job(job.id).status == "succeeded"
        await http.aclose()
    asyncio.run(run())


def test_complete_program_reuses_script_audio_and_cover_plan(tmp_path: Path) -> None:
    import base64
    fail = True

    def handler(request):
        if fail:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})
    service, graph, audio, planner, http, _, _ = setup_cover(tmp_path, handler)
    service.config.pipeline.auto_cover = True

    async def run():
        first = service.submit(ChatRequest(action="create", source_text="先保留音频"))
        await service.drain()
        session = service.store.get_session(first.session_id)
        assert session.last_job.partial is True
        nonlocal fail
        fail = False
        retry = service.submit(ChatRequest(
            action="complete_program", session_id=session.id,
            revision_id=session.current_revision.id,
        ))
        await service.drain()
        assert service.store.get_job(retry.id).status == "succeeded"
        assert service.store.get_job(retry.id).node_states["tts"] == "skipped"
        assert len(graph.inputs) == len(audio.calls) == len(planner.calls) == 1
        finished = service.store.get_session(session.id)
        assert finished.published_audio is not None and finished.published_cover is not None
        assert finished.current_revision.id == session.current_revision.id
        await http.aclose()
    asyncio.run(run())
