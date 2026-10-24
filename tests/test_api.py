import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_service import make_service
from test_store import example_script

from podcast_workbench.api import create_app
from podcast_workbench.models import Artifact, ProgramSettings


def wait_job(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        result = client.get(f"/jobs/{job_id}").json()
        if result["status"] in {"succeeded", "failed"}:
            return result
        time.sleep(0.02)
    raise AssertionError("test job did not finish")


def test_preview_session_restore_and_safe_artifact_download(tmp_path: Path) -> None:
    service, _, audio = make_service(tmp_path)
    with TestClient(create_app(service.config, service)) as client:
        submitted = client.post(
            "/chat",
            json={
                "action": "create",
                "source_text": "原始材料",
                "preview_only": True,
            },
        )
        assert submitted.status_code == 202
        data = submitted.json()
        job = wait_job(client, data["job_id"])
        assert job["status"] == "succeeded" and audio.calls == []
        assert job["trace_id"] == data["trace_id"]
        session = client.get(f"/sessions/{data['session_id']}").json()
        assert session["source_text"] == "原始材料"
        artifacts = client.get(f"/sessions/{data['session_id']}/artifacts").json()["artifacts"]
        assert len(artifacts) == 1 and "path" not in artifacts[0]
        result = client.get(artifacts[0]["url"] + "?download=true")
        assert result.status_code == 200 and "attachment" in result.headers["Content-Disposition"]
        assert "主持人" in result.text
        assert client.get("/").status_code == 200
        assert client.get("/docs").status_code == 404


def test_job_results_do_not_include_later_voice_renders(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    with TestClient(create_app(service.config, service)) as client:
        first = client.post("/chat", json={"action": "create", "source_text": "原文"}).json()
        original = wait_job(client, first["job_id"])
        original_ids = {item["id"] for item in original["artifacts"]}
        second = client.post(
            "/chat",
            json={
                "action": "render_audio",
                "session_id": first["session_id"],
                "settings": {"host_voice": "Serena"},
            },
        ).json()
        wait_job(client, second["job_id"])
        historical = client.get(f"/jobs/{first['job_id']}").json()
        assert {item["id"] for item in historical["artifacts"]} == original_ids


def test_errors_do_not_echo_input_and_missing_keys_are_explicit(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    service.config.llm.api_key = SecretStr("")
    with TestClient(create_app(service.config, service)) as client:
        invalid = client.post("/chat", json={"action": "revise", "message": "PRIVATE-INPUT"})
        assert invalid.status_code == 422 and "PRIVATE-INPUT" not in invalid.text
        assert invalid.json()["trace_id"]
        missing = client.post("/chat", json={"action": "create", "source_text": "材料"})
        assert missing.status_code == 503 and "DEEPSEEK_API_KEY" in missing.text
        unknown = client.get(f"/sessions/{uuid4()}")
        assert unknown.status_code == 404
        health = client.get("/health").json()
        assert health["llm_configured"] is False
        assert "test-only-tts" not in str(health)


def test_task_failure_and_audio_range_requests(tmp_path: Path) -> None:
    service, _, audio = make_service(tmp_path)
    with TestClient(create_app(service.config, service)) as client:
        data = client.post("/chat", json={"action": "create", "source_text": "原文"}).json()
        first = wait_job(client, data["job_id"])
        assert first["status"] == "succeeded"
        artifact = [item for item in first["artifacts"] if item["kind"] == "audio"][0]
        response = client.get(artifact["url"], headers={"Range": "bytes=0-43"})
        assert response.status_code == 206 and len(response.content) == 44
        audio.fail = True
        changed = client.post(
            "/chat",
            json={
                "action": "revise",
                "session_id": data["session_id"],
                "message": "缩短",
            },
        ).json()
        failed = wait_job(client, changed["job_id"])
        assert failed["status"] == "failed" and failed["error_code"] == "audio_error"
        assert client.get(artifact["url"]).status_code == 200


def test_file_index_cannot_expose_paths_outside_output(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    session = service.store.create_session("原文", ProgramSettings())
    revision = service.store.save_revision(session, example_script())
    path = tmp_path / "private.txt"
    path.write_text("must-not-be-served", encoding="utf-8")
    artifact = Artifact(revision_id=revision.id, kind="script", path=path, mime_type="text/plain")
    service.store.save_artifact(artifact)
    with TestClient(create_app(service.config, service)) as client:
        result = client.get(f"/artifacts/{artifact.id}")
        assert result.status_code == 404 and "must-not-be-served" not in result.text


def test_busy_lower_input_limit_and_sync_audio_error_are_readable(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    service.config.app.max_source_chars = 1
    with TestClient(create_app(service.config, service)) as client:
        too_long = client.post(
            "/chat",
            json={
                "action": "create",
                "source_text": "两个字",
                "preview_only": True,
            },
        )
        assert too_long.status_code == 422
        session = service.store.create_session("文", ProgramSettings())
        service.store.save_revision(session, example_script())
        service.store.create_job(session, "revise", uuid4())
        busy = client.post(
            "/chat",
            json={
                "action": "revise",
                "session_id": str(session),
                "message": "调整",
                "preview_only": True,
            },
        )
        assert busy.status_code == 409
        service.config.app.ffmpeg_path = "a-nonexistent-private-path"
        failed = client.post("/chat", json={"action": "create", "source_text": "文"})
        assert failed.status_code == 502
        assert "a-nonexistent-private-path" not in failed.text
        assert "access-control-allow-origin" not in failed.headers
