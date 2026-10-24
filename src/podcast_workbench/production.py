"""LangGraph 制作总图：脚本团队共享审校稿，音频与封面分支协同后汇合。"""

from typing import NotRequired, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from podcast_workbench.config import Settings
from podcast_workbench.covers import CoverWorkflow
from podcast_workbench.errors import ServiceError
from podcast_workbench.models import (
    Artifact,
    ChatRequest,
    CoverOptions,
    CoverPlan,
    ErrorCode,
    Job,
    ProgramSettings,
    Revision,
)
from podcast_workbench.observability import RunContext
from podcast_workbench.scripts import ScriptWorkflow
from podcast_workbench.store import Store


class AudioProducer(Protocol):
    async def render(
        self, revision: Revision, settings: ProgramSettings, ctx: RunContext,
    ) -> Artifact: ...


class ProductionState(TypedDict):
    revision: NotRequired[Revision]
    plan: NotRequired[CoverPlan]
    audio_error: NotRequired[ErrorCode]
    cover_error: NotRequired[ErrorCode]
    audio_warning: NotRequired[str]
    cover_warning: NotRequired[str]
    cover_done: NotRequired[bool]


class ProductionGraph:
    def __init__(
        self, config: Settings, store: Store, scripts: ScriptWorkflow,
        audio: AudioProducer, covers: CoverWorkflow | None,
    ) -> None:
        self.config, self.store, self.scripts = config, store, scripts
        self.audio, self.covers = audio, covers

    async def run(
        self, job: Job, request: ChatRequest, settings: ProgramSettings, ctx: RunContext,
    ) -> ProductionState:
        auto_cover = (request.auto_cover if request.auto_cover is not None
                      else self.config.pipeline.auto_cover)

        async def script_team(state: ProductionState) -> ProductionState:
            if request.action == "complete_program":
                assert job.revision_id is not None
                revision = self.store.get_revision(job.revision_id)
            else:
                revision = await self.scripts.run(job, request, ctx)
            ctx.revision_id = revision.id
            return {"revision": revision}

        async def audio(state: ProductionState) -> ProductionState:
            existing = self.store.get_session(job.session_id).published_audio
            if (request.action == "complete_program" and existing
                    and existing.revision_id == state["revision"].id and existing.path.is_file()):
                ctx.emit("node.skip", node="tts", status="skipped")
                ctx.emit("node.skip", node="merge", status="skipped")
                return {}
            ctx.emit("node.start", node="tts")
            try:
                artifact = await self.audio.render(state["revision"], settings, ctx)
                artifact = artifact.model_copy(update={"job_id": job.id})
                self.store.save_artifact(artifact)
                self.store.publish_audio(job.session_id, state["revision"].id, artifact.id)
            except Exception as error:
                code = error.code if isinstance(error, ServiceError) else "internal_error"
                ctx.emit("node.error", node="tts", status="failed", error_code=code)
                return {"audio_error": code,
                        "audio_warning": "音频制作未完成，已成功的脚本或封面会保留。"}
            ctx.emit("node.end", node="tts", status="succeeded")
            return {}

        async def cover_planner(state: ProductionState) -> ProductionState:
            session = self.store.get_session(job.session_id)
            existing = session.published_cover
            if (request.action == "complete_program" and existing
                    and existing.revision_id == state["revision"].id and existing.path.is_file()):
                ctx.emit("node.skip", node="cover_planner", status="skipped")
                ctx.emit("node.skip", node="image", status="skipped")
                return {"cover_done": True}
            if self.covers is None or not self.config.image.api_key.get_secret_value().strip():
                ctx.emit("node.skip", node="cover_planner", status="skipped")
                ctx.emit("node.error", node="image", status="failed", error_code="image_error")
                return {"cover_error": "image_error", "cover_warning": (
                    "封面未制作：请在本地 .env 填写 AGNES_API_KEY 并重启后台。已成功的结果已保留。"
                )}
            if request.action == "complete_program" and session.cover_plan is not None:
                ctx.emit("node.skip", node="cover_planner", status="skipped")
                return {"plan": session.cover_plan}
            try:
                plan = await self.covers.plan(state["revision"], CoverOptions(), ctx)
                self.store.save_cover_plan(plan)
                return {"plan": plan}
            except Exception as error:
                code = error.code if isinstance(error, ServiceError) else "internal_error"
                # CoverWorkflow 已记录模型错误，异常存储等情况也明确结束节点。
                ctx.emit("node.error", node="cover_planner", status="failed", error_code=code)
                ctx.emit("node.skip", node="image", status="skipped")
                return {"cover_error": code,
                        "cover_warning": "封面策划未完成，已成功的脚本或音频会保留。"}

        async def image(state: ProductionState) -> ProductionState:
            assert self.covers is not None
            ctx.emit("node.start", node="image")
            try:
                plan = state["plan"]
                artifact = await self.covers.image.execute(
                    state["revision"], CoverOptions(prompt=plan.prompt, style=plan.style),
                    self.config.app.output_dir, ctx,
                )
                self.store.save_artifact(artifact)
                self.store.publish_cover(job.session_id, state["revision"].id, artifact.id)
            except Exception as error:
                code = error.code if isinstance(error, ServiceError) else "internal_error"
                ctx.emit("node.error", node="image", status="failed", error_code=code)
                return {"cover_error": code,
                        "cover_warning": "封面生成未完成，已成功的脚本或音频会保留。"}
            ctx.emit("node.end", node="image", status="succeeded")
            return {}

        graph = StateGraph(ProductionState)
        graph.add_node("script_team", script_team)
        graph.add_edge(START, "script_team")
        if request.preview_only:
            graph.add_edge("script_team", END)
        else:
            graph.add_node("audio", audio)
            graph.add_edge("script_team", "audio")
            if auto_cover:
                # 分支作为子图执行，避免父图的 superstep 屏障让生图等待整段配音。
                cover_graph = StateGraph(ProductionState)
                cover_graph.add_node("cover_planner", cover_planner)
                cover_graph.add_node("image", image)
                cover_graph.add_node("cover_done", lambda state: {})
                cover_graph.add_edge(START, "cover_planner")
                cover_graph.add_conditional_edges(
                    "cover_planner",
                    lambda state: "cover_done" if (
                        state.get("cover_error") or state.get("cover_done")
                    ) else "image",
                    ["cover_done", "image"],
                )
                cover_graph.add_edge("image", "cover_done")
                cover_graph.add_edge("cover_done", END)
                graph.add_node("cover_team", cover_graph.compile())
                graph.add_node("finish", lambda state: {})
                graph.add_edge("script_team", "cover_team")
                graph.add_edge(["audio", "cover_team"], "finish")
                graph.add_edge("finish", END)
            else:
                graph.add_edge("audio", END)
        return cast(ProductionState, await graph.compile().ainvoke(
            {}, {"recursion_limit": self.config.pipeline.recursion_limit},
        ))
