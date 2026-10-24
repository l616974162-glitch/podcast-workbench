import asyncio
import base64
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from test_service import make_service
from test_store import example_script

from podcast_workbench.api import create_app
from podcast_workbench.covers import CoverWorkflow
from podcast_workbench.models import ChatRequest, CoverDraft, CoverOptions
from podcast_workbench.tools.agnes_image import AgnesImageClient


def png() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (1024, 1024), "#7052a3").save(stream, format="PNG")
    return stream.getvalue()


class Planner:
    def __init__(self):
        self.calls = []

    async def complete(self, prompt, payload, schema, ctx, node):
        self.calls.append(payload)
        return CoverDraft(
            prompt="A book and sound waves, square, no text.", rationale="依据阅读主题",
        )


def setup_cover(tmp_path, handler):
    service, graph, audio = make_service(tmp_path)
    service.config.image.api_key = SecretStr("test-image-key")
    planner = Planner()
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer inherited"},
    )
    service.covers = CoverWorkflow(
        service.config, planner, AgnesImageClient(service.config.image, http),
    )
    session_id = service.store.create_session("阅读的原文", example_settings())
    revision = service.store.save_revision(session_id, example_script())
    return service, graph, audio, planner, http, session_id, revision


def example_settings():
    from podcast_workbench.models import ProgramSettings
    return ProgramSettings()


def test_cover_plan_generate_restore_download_and_failure_preserve(tmp_path: Path) -> None:
    fail = False

    def handler(request):
        if fail:
            return httpx.Response(503, text="private upstream message")
        assert request.url.path == "/v1/images/generations"
        assert request.headers["Authorization"] == "Bearer test-image-key"
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})

    service, graph, audio, planner, http, sid, rev = setup_cover(tmp_path, handler)

    async def run():
        plan = service.submit(ChatRequest(action="plan_cover", session_id=sid, revision_id=rev.id))
        await service.drain()
        assert service.store.get_job(plan.id).status == "succeeded"
        assert service.store.get_session(sid).cover_plan.prompt.startswith("A book")
        image = service.submit(ChatRequest(
            action="generate_cover", session_id=sid, revision_id=rev.id,
            cover=CoverOptions(prompt="Square book cover without text."),
        ))
        await service.drain()
        assert service.store.get_job(image.id).status == "succeeded"
        cover = service.store.get_session(sid).published_cover
        assert cover.kind == "image" and cover.path.read_bytes().startswith(b"\x89PNG")
        assert cover.image_width == 1024 and cover.image_model == "agnes-image-2.5-flash"
        assert graph.inputs == [] and audio.calls == [] and len(planner.calls) == 1
        nonlocal fail
        fail = True
        retry = service.submit(ChatRequest(
            action="generate_cover", session_id=sid, revision_id=rev.id,
            cover=CoverOptions(prompt="Try another cover."),
        ))
        await service.drain()
        assert service.store.get_job(retry.id).error_code == "image_error"
        assert service.store.get_session(sid).published_cover.id == cover.id
        await http.aclose()

    asyncio.run(run())
    with TestClient(create_app(service.config, service)) as client:
        result = client.get(f"/sessions/{sid}").json()
        assert "path" not in result["published_cover"]
        download = client.get(result["published_cover"]["url"] + "?download=true")
        assert download.status_code == 200 and ".png" in download.headers["content-disposition"]
        service.store.save_revision(sid, example_script())
        assert client.get(f"/sessions/{sid}").json()["cover_is_stale"] is True
        assert client.get(f"/sessions/{sid}").json()["cover_plan"] is None


@pytest.mark.parametrize("payload", [
    {"action": "generate_cover", "session_id": str(uuid4()), "revision_id": str(uuid4())},
    {"action": "plan_cover", "session_id": str(uuid4())},
    {"action": "create", "source_text": "text", "cover": {"prompt": "bad branch"}},
])
def test_cover_action_validation(payload) -> None:
    with pytest.raises(ValueError):
        ChatRequest.model_validate(payload)


@pytest.mark.parametrize("mode", ["private_url", "invalid_image", "redirect", "oversize"])
def test_image_tool_rejects_unsafe_downloads_and_invalid_content(tmp_path: Path, mode: str) -> None:
    downloads = []

    def handler(request):
        if request.method == "POST":
            url = "http://127.0.0.1/secret" if mode == "private_url" else "https://storage.googleapis.com/test.png"
            return httpx.Response(200, json={"data": [{"url": url, "b64_json": ""}]})
        downloads.append(request)
        assert "Authorization" not in request.headers
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})
        if mode == "oversize":
            return httpx.Response(200, headers={"Content-Length": "999999999"}, content=b"x")
        return httpx.Response(200, content=b"not a PNG")

    service, _, _, _, http, sid, rev = setup_cover(tmp_path, handler)

    async def run():
        job = service.submit(ChatRequest(
            action="generate_cover", session_id=sid, revision_id=rev.id,
            cover=CoverOptions(prompt="No text cover"),
        ))
        await service.drain()
        assert service.store.get_job(job.id).error_code == "image_error"
        assert service.store.get_session(sid).published_cover is None
        assert not list(service.config.app.output_dir.rglob("*.part"))
        await http.aclose()

    asyncio.run(run())
    if mode == "private_url":
        assert downloads == []


def test_cover_stale_request_and_busy_session_rejected(tmp_path: Path) -> None:
    from podcast_workbench.store import BusySessionError, StateConflictError
    service, _, _, _, http, sid, rev = setup_cover(tmp_path, lambda r: httpx.Response(503))

    async def run():
        with pytest.raises(StateConflictError):
            service.submit(ChatRequest(action="plan_cover", session_id=sid, revision_id=uuid4()))
        job = service.submit(ChatRequest(action="plan_cover", session_id=sid, revision_id=rev.id))
        with pytest.raises(BusySessionError):
            service.submit(ChatRequest(action="plan_cover", session_id=sid, revision_id=rev.id))
        await service.drain()
        assert service.store.get_job(job.id).status == "succeeded"
        await http.aclose()
    asyncio.run(run())


def test_cover_api_plan_payload_job_isolation_and_missing_image_key(tmp_path: Path) -> None:
    from test_api import wait_job
    service, _, _, _, http, sid, rev = setup_cover(tmp_path, lambda r: httpx.Response(503))
    service.config.image.api_key = SecretStr("")
    with TestClient(create_app(service.config, service)) as client:
        assert client.get("/health").json()["image_configured"] is False
        accepted = client.post("/chat", json={
            "action": "plan_cover", "session_id": str(sid), "revision_id": str(rev.id),
            "cover": {"style": "abstract"},
        })
        assert accepted.status_code == 202
        job = wait_job(client, accepted.json()["job_id"])
        assert job["cover_plan"]["style"] == "abstract"
        assert job["artifacts"] == []
        missing = client.post("/chat", json={
            "action": "generate_cover", "session_id": str(sid), "revision_id": str(rev.id),
            "cover": {"prompt": "No text cover"},
        })
        assert missing.status_code == 503
        assert "AGNES_API_KEY" in missing.json()["error"]["message"]
        service.store.save_revision(sid, example_script())
        assert client.get(f"/jobs/{job['id']}").json()["cover_plan"]["revision_id"] == str(rev.id)
    asyncio.run(http.aclose())


def test_image_url_download_succeeds_without_forwarding_auth(tmp_path: Path) -> None:
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{
                "url": "https://storage.googleapis.com/agnes-aigc/sample.png", "b64_json": "",
            }]})
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=png())
    service, _, _, _, http, sid, rev = setup_cover(tmp_path, handler)

    async def run():
        job = service.submit(ChatRequest(
            action="generate_cover", session_id=sid, revision_id=rev.id,
            cover=CoverOptions(prompt="No text cover"),
        ))
        await service.drain()
        assert service.store.get_job(job.id).status == "succeeded"
        await http.aclose()
    asyncio.run(run())


def test_revision_guard_is_checked_inside_job_transaction(tmp_path: Path) -> None:
    from podcast_workbench.store import StateConflictError
    service, _, _ = make_service(tmp_path)
    sid = service.store.create_session("原文", example_settings())
    old = service.store.save_revision(sid, example_script())
    service.store.save_revision(sid, example_script())
    with pytest.raises(StateConflictError):
        service.store.create_job(sid, "plan_cover", uuid4(), expected_revision_id=old.id)
    assert service.store.get_session(sid).active_job is None
