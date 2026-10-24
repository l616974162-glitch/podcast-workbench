"""材料与设置保持、对话修改和脚本产物保存。"""

from pathlib import Path
from typing import Protocol

from podcast_workbench.models import Artifact, ChatRequest, Job, ProgramSettings, Revision, Script
from podcast_workbench.observability import RunContext
from podcast_workbench.store import Store


class ScriptGenerator(Protocol):
    async def generate(self, payload: dict[str, object], ctx: RunContext) -> Script: ...


def merge_settings(base: ProgramSettings, override: ProgramSettings | None) -> ProgramSettings:
    return ProgramSettings.model_validate({
        **base.model_dump(), **(override.model_dump(exclude_unset=True) if override else {}),
    })


class ScriptWorkflow:
    def __init__(self, store: Store, graph: ScriptGenerator, output_dir: Path) -> None:
        self.store = store
        self.graph = graph
        self.output_dir = output_dir.resolve()

    async def run(self, job: Job, request: ChatRequest, ctx: RunContext) -> Revision:
        session = self.store.get_session(job.session_id)
        selected = merge_settings(session.settings, request.settings)
        if request.action == "revise":
            self.store.append_message(session.id, "user", request.message)
        payload: dict[str, object] = {
            "source_text": session.source_text,
            "settings": selected.model_dump(mode="json"),
            "revision_request": request.message,
            "current_script": (session.current_revision.script.model_dump(mode="json")
                               if session.current_revision else None),
            "conversation": [message.model_dump(mode="json")
                             for message in session.messages[1:][-8:]],
            "length_guidance": "按目标时长安排内容，中文口语约每分钟 200–260 字，勿为凑时长编造。",
        }
        script = await self.graph.generate(payload, ctx)
        revision = self.store.save_revision(session.id, script, settings=selected, job_id=job.id)
        ctx.revision_id = revision.id
        directory = self.output_dir / str(revision.id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "script.txt"
        text = script.title + "\n\n" + script.summary + "\n\n" + "\n\n".join(
            (selected.host_name if turn.role == "host" else selected.guest_name) + "：" + turn.text
            for turn in script.turns
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        self.store.save_artifact(Artifact(
            revision_id=revision.id, job_id=job.id, kind="script", path=path,
            mime_type="text/plain",
        ))
        self.store.append_message(
            session.id, "assistant", f"已生成第 {revision.number} 版：{script.title}",
        )
        return revision
