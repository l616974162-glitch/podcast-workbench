from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from podcast_workbench.models import Artifact, ChatRequest, ProgramSettings, Script, Turn


@pytest.mark.parametrize(
    "source", ["", " \n\t ", "文" * 20001], ids=["empty", "blank", "oversized"],
)
def test_create_rejects_empty_or_oversized_material(source: str) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(action="create", source_text=source)


def test_create_normalizes_text_and_uses_program_defaults() -> None:
    request = ChatRequest(action="create", source_text="  一篇材料。  ")
    assert request.source_text == "一篇材料。"
    settings = ProgramSettings()
    assert settings.target_minutes == 3
    assert settings.host_voice != settings.guest_voice


@pytest.mark.parametrize("action", ["revise", "render_audio"])
def test_followup_requires_session(action: str) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"action": action, "message": "缩短节目"})


def test_revision_requires_instructions_and_preserves_optional_settings() -> None:
    session_id = uuid4()
    with pytest.raises(ValidationError):
        ChatRequest(action="revise", session_id=session_id, message="   ")
    request = ChatRequest(action="revise", session_id=session_id, message=" 缩短节目 ")
    assert request.message == "缩短节目"
    assert request.settings is None


@pytest.mark.parametrize("minutes", [0, 4, 10])
def test_program_rejects_unsupported_duration(minutes: int) -> None:
    with pytest.raises(ValidationError):
        ProgramSettings.model_validate({"target_minutes": minutes})


def test_create_cannot_replace_existing_session_material() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(action="create", session_id=uuid4(), source_text="另一篇材料")


def test_followup_cannot_silently_replace_material() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(action="revise", session_id=uuid4(), source_text="新材料", message="修改")


def test_script_requires_both_roles() -> None:
    with pytest.raises(ValidationError):
        Script(title="节目", summary="简介", turns=[
            Turn(role="host", text="开场。"), Turn(role="host", text="还是同一个角色。"),
        ])
    script = Script(title="节目", summary="简介", turns=[
        Turn(role="host", text="介绍一下？"), Turn(role="guest", text="好的。"),
    ])
    assert len(script.turns) == 2


def test_audio_metadata_requires_duration_and_audio_mime(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Artifact(revision_id=uuid4(), kind="audio", path=tmp_path / "a.wav",
                 mime_type="text/plain", duration_seconds=0)


def test_audio_records_the_voice_pair_used_for_that_render(tmp_path: Path) -> None:
    settings = ProgramSettings(host_voice="Serena", guest_voice="Ethan")
    artifact = Artifact(
        revision_id=uuid4(), kind="audio", path=tmp_path / "a.wav",
        mime_type="audio/wav", duration_seconds=1, audio_settings=settings,
    )
    restored = Artifact.model_validate_json(artifact.model_dump_json())
    assert restored.audio_settings.host_voice == "Serena"


def test_validation_error_does_not_echo_material() -> None:
    with pytest.raises(ValidationError) as error:
        ChatRequest(action="revise", message="private-material-that-is-not-for-errors")
    assert "private-material-that-is-not-for-errors" not in str(error.value)
