"""本地工作台页面与同源业务 API。"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from podcast_workbench.config import ConfigError, Settings, load_config
from podcast_workbench.documents import export_script
from podcast_workbench.errors import ServiceError
from podcast_workbench.media import resolve_ffmpeg
from podcast_workbench.models import Artifact, ChatRequest
from podcast_workbench.service import RequestError, WorkbenchService, create_service
from podcast_workbench.store import NotFoundError, StateConflictError


def create_app(
    settings: Settings | None = None,
    service: WorkbenchService | None = None,
) -> FastAPI:
    config = settings or load_config(Path("config.yaml"))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        http: httpx.AsyncClient | None = None
        if service is None:
            http = httpx.AsyncClient(trust_env=False)
            workbench = create_service(config, http)
        else:
            workbench = service
        workbench.store.recover_interrupted_jobs()
        app.state.workbench = workbench
        try:
            yield
        finally:
            await workbench.drain()
            if http is not None:
                await http.aclose()

    app = FastAPI(
        title="双人播客工作台后端",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    async def home() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    def error(request: Request, status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={
                "trace_id": str(request.state.trace_id),
                "error": {"code": code, "message": message},
            },
        )

    @app.middleware("http")
    async def trace(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.trace_id = uuid4()
        try:
            response = await call_next(request)
        except Exception:
            response = error(request, 500, "internal_error", "内部处理失败，请检查本地环境。")
        response.headers["X-Trace-ID"] = str(request.state.trace_id)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error(
            request, 422, "invalid_request", "输入不合法，请检查材料、会话标识和节目设置。"
        )

    @app.exception_handler(NotFoundError)
    async def missing(request: Request, exc: NotFoundError) -> JSONResponse:
        return error(request, 404, "not_found", "指定会话、任务或产物不存在。")

    @app.exception_handler(StateConflictError)
    async def conflict(request: Request, exc: StateConflictError) -> JSONResponse:
        return error(request, 409, "conflict", str(exc))

    @app.exception_handler(ConfigError)
    async def config_error(request: Request, exc: ConfigError) -> JSONResponse:
        return error(request, 503, "not_configured", str(exc))

    @app.exception_handler(RequestError)
    async def bad_request(request: Request, exc: RequestError) -> JSONResponse:
        return error(request, 422, "invalid_request", str(exc))

    @app.exception_handler(ServiceError)
    async def upstream(request: Request, exc: ServiceError) -> JSONResponse:
        return error(request, 502, exc.code, str(exc))

    def public_artifact(artifact: Artifact) -> dict[str, object]:
        available = (
            artifact.path.resolve().is_relative_to(config.app.output_dir.resolve())
            and artifact.path.is_file()
        )
        return {
            **artifact.model_dump(mode="json", exclude={"path"}),
            "available": available,
            "url": f"/artifacts/{artifact.id}" if available else None,
        }

    @app.post("/chat", status_code=202)
    async def chat(body: ChatRequest, request: Request) -> dict[str, object]:
        workbench: WorkbenchService = request.app.state.workbench
        job = workbench.submit(body, request.state.trace_id)
        return {
            "job_id": str(job.id),
            "session_id": str(job.session_id),
            "trace_id": str(job.trace_id),
            "status": job.status,
        }

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: UUID, request: Request) -> dict[str, object]:
        workbench: WorkbenchService = request.app.state.workbench
        job = workbench.store.get_job(job_id)
        artifacts = workbench.store.list_artifacts(job.session_id) if job.revision_id else []
        return {
            **job.model_dump(mode="json"),
            "artifacts": [public_artifact(item) for item in artifacts if item.job_id == job.id],
            "cover_plan": (
                plan.model_dump(mode="json")
                if (plan := workbench.store.get_cover_plan(job.id)) else None
            ),
        }

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: UUID, request: Request) -> dict[str, object]:
        workbench: WorkbenchService = request.app.state.workbench
        session = workbench.store.get_session(session_id)
        return {
            **session.model_dump(mode="json", exclude={"published_audio", "published_cover"}),
            "session_id": str(session.id),
            "trace_id": str(request.state.trace_id),
            "published_audio": public_artifact(session.published_audio)
            if session.published_audio
            else None,
            "published_cover": public_artifact(session.published_cover)
            if session.published_cover else None,
        }

    @app.get("/sessions/{session_id}/artifacts")
    async def get_artifacts(session_id: UUID, request: Request) -> dict[str, object]:
        workbench: WorkbenchService = request.app.state.workbench
        return {
            "session_id": str(session_id),
            "trace_id": str(request.state.trace_id),
            "artifacts": [
                public_artifact(item) for item in workbench.store.list_artifacts(session_id)
            ],
        }

    @app.get("/artifacts/{artifact_id}")
    async def get_artifact(
        artifact_id: UUID,
        request: Request,
        download: bool = False,
    ) -> FileResponse:
        workbench: WorkbenchService = request.app.state.workbench
        artifact = workbench.store.get_artifact(artifact_id)
        if not public_artifact(artifact)["available"]:
            raise NotFoundError("产物不可用。")
        extension = {"audio": ".wav", "script": ".txt", "image": ".png"}[artifact.kind]
        return FileResponse(
            artifact.path,
            media_type=artifact.mime_type,
            filename=str(artifact.id) + extension,
            content_disposition_type="attachment" if download else "inline",
        )

    @app.get("/revisions/{revision_id}/document")
    def get_document(revision_id: UUID, request: Request) -> Response:
        workbench: WorkbenchService = request.app.state.workbench
        revision = workbench.store.get_revision(revision_id)
        return Response(
            export_script(revision),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="script-v{revision.number}.docx"',
            },
        )

    @app.get("/health")
    async def health(request: Request) -> dict[str, object]:
        try:
            resolve_ffmpeg(config.app.ffmpeg_path)
            ffmpeg_available = True
        except ServiceError:
            ffmpeg_available = False
        return {
            "status": "ok",
            "scope": "workbench",
            "trace_id": str(request.state.trace_id),
            "llm_configured": bool(config.llm.api_key.get_secret_value().strip()),
            "tts_configured": (
                not config.tts.needs_api_key or bool(config.tts.api_key.get_secret_value().strip())
            ),
            "tts_protocol": config.tts.protocol,
            "default_voices": config.tts.default_voices(),
            "ffmpeg_available": ffmpeg_available,
            "frontend_connected": True,
            "image_configured": bool(config.image.api_key.get_secret_value().strip()),
            "image_model": config.image.model,
            "max_source_chars": min(config.app.max_source_chars, 20000),
        }

    return app
