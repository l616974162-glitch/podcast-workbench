"""后台制作任务与阶段协调，不包含网页连接或浏览器操作。"""

import asyncio
from typing import Protocol
from uuid import UUID, uuid4

import httpx

from podcast_workbench.audio import AudioRenderer
from podcast_workbench.config import ConfigError, Settings
from podcast_workbench.errors import ServiceError
from podcast_workbench.graph import PodcastGraph
from podcast_workbench.llm import DeepSeekClient
from podcast_workbench.media import resolve_ffmpeg
from podcast_workbench.models import (
    Artifact,
    ChatRequest,
    ErrorCode,
    Job,
    ProgramSettings,
    Revision,
    Stage,
)
from podcast_workbench.observability import EventLogger, RunContext
from podcast_workbench.scripts import ScriptWorkflow, merge_settings
from podcast_workbench.store import BusySessionError, StateConflictError, Store
from podcast_workbench.tools.registry import create_tts_client


class RequestError(ValueError):
    """可读的请求语义错误，不附带原始用户材料。"""


class Renderer(Protocol):
    async def render(
        self,
        revision: Revision,
        settings: ProgramSettings,
        ctx: RunContext,
    ) -> Artifact: ...


class WorkbenchService:
    def __init__(
        self,
        config: Settings,
        store: Store,
        scripts: ScriptWorkflow,
        audio: Renderer,
        logger: EventLogger,
    ) -> None:
        self.config = config
        self.store = store
        self.scripts = scripts
        self.audio = audio
        self.logger = logger
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    def submit(self, request: ChatRequest, trace_id: UUID | None = None) -> Job:
        needs_text = request.action != "render_audio"
        needs_audio = request.action == "render_audio" or not request.preview_only
        if needs_text and not self.config.llm.api_key.get_secret_value().strip():
            raise ConfigError("请在本地 .env 填写 DEEPSEEK_API_KEY。")
        if needs_audio:
            if not self.config.tts.api_key.get_secret_value().strip():
                raise ConfigError("请在本地 .env 填写 DASHSCOPE_API_KEY。")
            resolve_ffmpeg(self.config.app.ffmpeg_path)
        if request.source_text and len(request.source_text) > self.config.app.max_source_chars:
            raise RequestError("材料超过当前配置的字符上限，请缩短后重试。")
        if len(self._tasks) >= 4:
            raise BusySessionError("当前制作任务较多，请等待已有任务完成。")
        if request.action == "render_audio" and (request.preview_only or request.message):
            raise RequestError("生成音频只接受音色设置；修改内容请使用 revise。")
        if request.session_id is not None:
            session = self.store.get_session(request.session_id)
            if request.action == "render_audio" and session.current_revision is None:
                raise StateConflictError("该会话尚无脚本，请先生成脚本。")
            selected = merge_settings(session.settings, request.settings)
            if request.action == "render_audio":
                requested = (
                    request.settings.model_dump(exclude_unset=True) if request.settings else {}
                )
                if set(requested) - {"host_voice", "guest_voice"}:
                    raise RequestError("仅制作音频不能改变听众、风格或时长，请使用 revise。")
            session_id = session.id
        else:
            selected = merge_settings(
                ProgramSettings(
                    host_voice=self.config.tts.host_voice,
                    guest_voice=self.config.tts.guest_voice,
                ),
                request.settings,
            )
            if not request.source_text:
                raise RequestError("请提供文本材料。")
            session_id = None
        if needs_audio and selected.host_voice == selected.guest_voice:
            raise RequestError("主持人与嘉宾需要选择不同音色。")
        asyncio.get_running_loop()
        if session_id is None:
            assert request.source_text is not None
            session_id = self.store.create_session(request.source_text, selected)
        job = self.store.create_job(session_id, request.action, trace_id or uuid4())
        task = asyncio.create_task(self._run(job, request, selected))
        self._tasks[job.id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job.id, None))
        return job

    async def _run(self, job: Job, request: ChatRequest, selected: ProgramSettings) -> None:
        def stage(value: Stage) -> None:
            self.store.transition_job(job.id, "running", stage=value)

        ctx = RunContext(
            job.trace_id,
            job.session_id,
            job.id,
            self.logger,
            revision_id=job.revision_id,
            on_stage=stage,
        )
        try:
            self.store.transition_job(job.id, "running")
            ctx.emit("run.start")
            if request.action == "render_audio":
                assert job.revision_id is not None
                revision = self.store.get_revision(job.revision_id)
            else:
                revision = await self.scripts.run(job, request, ctx)
            ctx.revision_id = revision.id
            if request.action == "render_audio" or not request.preview_only:
                artifact = await self.audio.render(revision, selected, ctx)
                artifact = artifact.model_copy(update={"job_id": job.id})
                self.store.save_artifact(artifact)
                self.store.publish_audio(job.session_id, revision.id, artifact.id)
            ctx.emit("run.end", status="succeeded")
            self.store.transition_job(job.id, "succeeded")
        except asyncio.CancelledError:
            self._fail(job, ctx, "interrupted")
            raise
        except ServiceError as error:
            self._fail(job, ctx, error.code)
        except Exception:
            self._fail(job, ctx, "internal_error")

    def _fail(self, job: Job, ctx: RunContext, code: ErrorCode) -> None:
        current = self.store.get_job(job.id)
        if current.status in {"queued", "running"}:
            self.store.transition_job(job.id, "failed", error_code=code)
        try:
            ctx.emit("run.error", status="failed", error_code=code)
        except OSError:
            pass

    async def drain(self) -> None:
        """优雅退出时等待制作任务；强制终止的任务在下次启动标记中断。"""
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)


def create_service(config: Settings, http: httpx.AsyncClient) -> WorkbenchService:
    store = Store(config.app.database_path)
    return WorkbenchService(
        config,
        store,
        ScriptWorkflow(
            store, PodcastGraph(config, DeepSeekClient(config.llm, http)), config.app.output_dir
        ),
        AudioRenderer(config, create_tts_client(config.tts, http), config.app.output_dir),
        EventLogger(config.app.log_dir / "agent.log"),
    )
