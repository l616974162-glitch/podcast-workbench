import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from test_llm import context
from test_store import example_script

from podcast_workbench.errors import ServiceError
from podcast_workbench.models import ChatRequest, ProgramSettings
from podcast_workbench.scripts import ScriptWorkflow
from podcast_workbench.store import Store


class CapturingGraph:
    def __init__(self):
        self.inputs = []
        self.fail = False

    async def generate(self, payload, ctx):
        self.inputs.append(payload)
        if self.fail:
            raise ServiceError("invalid_output")
        return example_script(f"第{len(self.inputs)}稿")


def test_two_revisions_keep_material_and_merge_settings(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    session_id = store.create_session("始终保留的原文", ProgramSettings(target_minutes=5))
    graph = CapturingGraph()
    workflow = ScriptWorkflow(store, graph, tmp_path / "output")

    async def run() -> None:
        for message, settings in [("更通俗", None), ("缩短", ProgramSettings(target_minutes=2))]:
            job = store.create_job(session_id, "revise", uuid4())
            store.transition_job(job.id, "running")
            request = ChatRequest(action="revise", session_id=session_id,
                                  message=message, settings=settings)
            await workflow.run(job, request, context(tmp_path))
            store.transition_job(job.id, "succeeded")

    asyncio.run(run())
    assert all(item["source_text"] == "始终保留的原文" for item in graph.inputs)
    assert graph.inputs[0]["settings"]["target_minutes"] == 5
    assert graph.inputs[1]["settings"]["target_minutes"] == 2
    assert graph.inputs[1]["current_script"]["title"] == "第1稿"
    assert graph.inputs[1]["revision_request"] == "缩短"
    current = store.get_session(session_id)
    assert current.current_revision.number == 2
    artifacts = store.list_artifacts(session_id)
    assert len(artifacts) == 2 and all(item.kind == "script" for item in artifacts)
    assert "主持人" in artifacts[-1].path.read_text(encoding="utf-8")


def test_failed_generation_leaves_latest_script_intact(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    session = store.create_session("原文", ProgramSettings())
    first = store.save_revision(session, example_script())
    graph = CapturingGraph()
    graph.fail = True
    job = store.create_job(session, "revise", uuid4())
    store.transition_job(job.id, "running")
    with pytest.raises(ServiceError):
        asyncio.run(ScriptWorkflow(store, graph, tmp_path / "output").run(
            job, ChatRequest(action="revise", session_id=session, message="调整"),
            context(tmp_path),
        ))
    assert store.get_session(session).current_revision.id == first.id


def test_voice_only_override_preserves_other_settings() -> None:
    from podcast_workbench.scripts import merge_settings
    settings = merge_settings(
        ProgramSettings(target_minutes=5, audience="professional"),
        ProgramSettings(host_voice="Serena"),
    )
    assert settings.target_minutes == 5 and settings.audience == "professional"
    assert settings.host_voice == "Serena"
