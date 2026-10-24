from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from podcast_workbench.models import Artifact, ProgramSettings, Script, Turn
from podcast_workbench.store import BusySessionError, NotFoundError, StateConflictError, Store


def example_script(title: str = "第一版") -> Script:
    return Script(title=title, summary="测试简介", turns=[
        Turn(role="host", text="今天讨论什么？"), Turn(role="guest", text="讨论原文中的事实。"),
    ])


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "data" / "test.sqlite3")


def test_sessions_settings_messages_and_revision_survive_reopen(store: Store) -> None:
    first = store.create_session("第一份材料", ProgramSettings(target_minutes=2))
    second = store.create_session("第二份材料", ProgramSettings(target_minutes=5))
    store.append_message(first, "user", "讲得简单一点")
    revision = store.save_revision(first, example_script())
    reopened = Store(store.path)
    session = reopened.get_session(first)
    assert session.source_text == "第一份材料"
    assert session.settings.target_minutes == 2
    assert session.current_revision == revision
    assert session.messages[-1].content == "讲得简单一点"
    assert reopened.get_session(second).current_revision is None
    assert reopened.get_session(second).settings.target_minutes == 5


def test_revision_is_snapshot_and_numbers_increase(store: Store) -> None:
    session = store.create_session("材料", ProgramSettings())
    script = example_script()
    old = store.save_revision(session, script)
    script.turns.append(Turn(role="host", text="这段不能改变已经保存的旧版本"))
    latest = store.save_revision(session, example_script("第二版"),
                                 settings=ProgramSettings(target_minutes=2))
    assert old.number == 1 and latest.number == 2
    assert len(store.get_revision(old.id).script.turns) == 2
    assert store.get_revision(old.id).settings.target_minutes == 3
    assert store.get_session(session).settings.target_minutes == 2


def test_only_one_active_job_across_store_instances(store: Store) -> None:
    session = store.create_session("材料", ProgramSettings())
    first = store.create_job(session, "create", uuid4())
    with pytest.raises(BusySessionError):
        Store(store.path).create_job(session, "revise", uuid4())
    store.transition_job(first.id, "running", stage="researcher")
    store.transition_job(first.id, "failed", error_code="upstream_error")
    retry = store.create_job(session, "revise", uuid4())
    assert store.get_session(session).active_job == retry


def test_concurrent_job_creation_has_exactly_one_winner(store: Store) -> None:
    session = store.create_session("材料", ProgramSettings())

    def submit(_: int) -> str:
        try:
            Store(store.path).create_job(session, "create", uuid4())
            return "created"
        except BusySessionError:
            return "busy"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sorted(results) == ["busy", "created"]


def test_terminal_job_cannot_be_restarted(store: Store) -> None:
    session = store.create_session("材料", ProgramSettings())
    job = store.create_job(session, "create", uuid4())
    store.transition_job(job.id, "running")
    store.transition_job(job.id, "succeeded")
    with pytest.raises(StateConflictError):
        store.transition_job(job.id, "running")


def test_job_keeps_its_revision_when_newer_scripts_are_saved(store: Store) -> None:
    session = store.create_session("材料", ProgramSettings())
    first = store.save_revision(session, example_script())
    job = store.create_job(session, "revise", uuid4())
    assert job.revision_id == first.id
    store.transition_job(job.id, "running", stage="writer")
    second = store.save_revision(session, example_script("修改稿"), job_id=job.id)
    store.transition_job(job.id, "succeeded")
    store.save_revision(session, example_script("下一版"))
    assert store.get_job(job.id).revision_id == second.id


def test_revision_cannot_be_attached_to_another_sessions_job(store: Store) -> None:
    first = store.create_session("材料一", ProgramSettings())
    second = store.create_session("材料二", ProgramSettings())
    job = store.create_job(first, "create", uuid4())
    store.transition_job(job.id, "running")
    with pytest.raises(StateConflictError):
        store.save_revision(second, example_script(), job_id=job.id)
    assert store.get_session(second).current_revision is None


def test_recovery_marks_interrupted_jobs_failed_and_releases_session(store: Store) -> None:
    session = store.create_session("材料", ProgramSettings())
    job = store.create_job(session, "create", uuid4())
    store.transition_job(job.id, "running", stage="writer")
    assert Store(store.path).recover_interrupted_jobs() == 1
    recovered = store.get_job(job.id)
    assert recovered.status == "failed"
    assert recovered.error_code == "interrupted"
    assert store.get_session(session).active_job is None


def test_failed_new_revision_preserves_previous_published_audio(
    store: Store, tmp_path: Path
) -> None:
    session = store.create_session("材料", ProgramSettings())
    old = store.save_revision(session, example_script())
    audio_path = tmp_path / "complete.wav"
    audio_path.write_bytes(b"test-only-audio-file")
    audio = Artifact(revision_id=old.id, kind="audio", path=audio_path,
                     mime_type="audio/wav", duration_seconds=1, audio_settings=ProgramSettings())
    store.save_artifact(audio)
    store.publish_audio(session, old.id, audio.id)
    latest = store.save_revision(session, example_script("第二版"))
    job = store.create_job(session, "render_audio", uuid4())
    store.transition_job(job.id, "running")
    store.transition_job(job.id, "failed", error_code="upstream_error")
    result = store.get_session(session)
    assert result.current_revision.id == latest.id
    assert result.published_audio.id == audio.id
    assert result.audio_is_stale is True
    assert store.list_artifacts(session) == [audio]


def test_audio_publication_rejects_wrong_session_old_revision_and_missing_file(
    store: Store, tmp_path: Path
) -> None:
    session = store.create_session("材料一", ProgramSettings())
    other = store.create_session("材料二", ProgramSettings())
    old = store.save_revision(session, example_script())
    audio = Artifact(revision_id=old.id, kind="audio", path=tmp_path / "missing.wav",
                     mime_type="audio/wav", duration_seconds=1, audio_settings=ProgramSettings())
    store.save_artifact(audio)
    with pytest.raises(StateConflictError):
        store.publish_audio(other, old.id, audio.id)
    with pytest.raises(StateConflictError):
        store.publish_audio(session, old.id, audio.id)
    audio.path.write_bytes(b"test-only-file")
    store.save_revision(session, example_script("新版"))
    with pytest.raises(StateConflictError):
        store.publish_audio(session, old.id, audio.id)
    assert store.get_session(session).published_audio is None


def test_artifact_ids_cannot_overwrite_existing_index(store: Store, tmp_path: Path) -> None:
    session = store.create_session("材料", ProgramSettings())
    revision = store.save_revision(session, example_script())
    artifact = Artifact(revision_id=revision.id, kind="script", path=tmp_path / "s.txt",
                        mime_type="text/plain")
    store.save_artifact(artifact)
    with pytest.raises(StateConflictError):
        store.save_artifact(artifact)


def test_unknown_and_sql_like_ids_do_not_change_data(store: Store) -> None:
    session = store.create_session("不能丢失的材料", ProgramSettings())
    with pytest.raises(NotFoundError):
        store.get_session("' OR 1=1; DROP TABLE sessions; --")
    with pytest.raises(NotFoundError):
        store.create_job(uuid4(), "create", uuid4())
    assert store.get_session(session).source_text == "不能丢失的材料"
