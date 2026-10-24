"""SQLite 会话、不可变脚本快照、任务互斥及成品索引。"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, TypeVar, cast
from uuid import UUID, uuid4

from pydantic import BaseModel

from podcast_workbench.models import (
    Action,
    Artifact,
    ChatRequest,
    ErrorCode,
    Job,
    JobStatus,
    Message,
    ProgramSettings,
    Revision,
    Script,
    Session,
    Stage,
    utc_now,
)

Id = UUID | str
T = TypeVar("T", bound=BaseModel)


class NotFoundError(LookupError):
    """指定记录不存在。"""


class StateConflictError(ValueError):
    """当前状态不允许此操作。"""


class BusySessionError(StateConflictError):
    """同一会话已有运行中的任务。"""


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    source_text TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    current_revision_id TEXT REFERENCES revisions(id),
    published_audio_id TEXT REFERENCES artifacts(id)
);
CREATE TABLE IF NOT EXISTS revisions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    number INTEGER NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE(session_id, number)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed')),
    payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_session
    ON jobs(session_id) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES revisions(id),
    payload TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection(write=True) as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _session_row(conn: sqlite3.Connection, session_id: Id) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (str(session_id),)).fetchone()
        if row is None:
            raise NotFoundError("会话不存在。")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _load(
        conn: sqlite3.Connection,
        table: Literal["revisions", "jobs", "artifacts"],
        record_id: Id,
        model: type[T],
    ) -> T:
        row = conn.execute(f"SELECT payload FROM {table} WHERE id=?", (str(record_id),)).fetchone()
        if row is None:
            raise NotFoundError("记录不存在。")
        return model.model_validate_json(row["payload"])

    def create_session(self, source_text: str, settings: ProgramSettings) -> UUID:
        request = ChatRequest(action="create", source_text=source_text, settings=settings)
        session_id = uuid4()
        assert request.source_text is not None
        with self._connection(write=True) as conn:
            conn.execute(
                "INSERT INTO sessions(id,source_text,settings_json) VALUES(?,?,?)",
                (str(session_id), request.source_text, settings.model_dump_json()),
            )
            message = Message(role="user", content=request.source_text)
            conn.execute("INSERT INTO messages(session_id,payload) VALUES(?,?)", (
                str(session_id), message.model_dump_json(),
            ))
        return session_id

    def append_message(
        self, session_id: Id, role: Literal["user", "assistant"], content: str,
    ) -> None:
        message = Message(role=role, content=content)
        with self._connection(write=True) as conn:
            self._session_row(conn, session_id)
            conn.execute("INSERT INTO messages(session_id,payload) VALUES(?,?)", (
                str(session_id), message.model_dump_json(),
            ))

    def save_revision(
        self, session_id: Id, script: Script, *, settings: ProgramSettings | None = None,
        job_id: Id | None = None,
    ) -> Revision:
        with self._connection(write=True) as conn:
            row = self._session_row(conn, session_id)
            job = self._load(conn, "jobs", job_id, Job) if job_id is not None else None
            if job and (str(job.session_id) != str(session_id) or job.status != "running"):
                raise StateConflictError("脚本只能关联本会话正在执行的任务。")
            next_number = conn.execute(
                "SELECT COALESCE(MAX(number),0)+1 FROM revisions WHERE session_id=?",
                (str(session_id),),
            ).fetchone()[0]
            selected = settings or ProgramSettings.model_validate_json(row["settings_json"])
            revision = Revision(session_id=UUID(str(session_id)), number=next_number,
                                script=script, settings=selected)
            conn.execute("INSERT INTO revisions VALUES(?,?,?,?)", (
                str(revision.id), str(session_id), revision.number, revision.model_dump_json(),
            ))
            conn.execute("UPDATE sessions SET current_revision_id=?,settings_json=? WHERE id=?", (
                str(revision.id), selected.model_dump_json(), str(session_id),
            ))
            if job:
                updated = job.model_copy(update={
                    "revision_id": revision.id, "updated_at": utc_now(),
                })
                conn.execute("UPDATE jobs SET payload=? WHERE id=?", (
                    updated.model_dump_json(exclude={"error_message"}), str(job.id),
                ))
            return revision

    def get_revision(self, revision_id: Id) -> Revision:
        with self._connection() as conn:
            return self._load(conn, "revisions", revision_id, Revision)

    def create_job(self, session_id: Id, action: Action, trace_id: UUID) -> Job:
        with self._connection(write=True) as conn:
            session = self._session_row(conn, session_id)
            revision_id = session["current_revision_id"]
            job = Job(session_id=UUID(str(session_id)), action=action, trace_id=trace_id,
                      revision_id=UUID(revision_id) if revision_id else None)
            try:
                conn.execute("INSERT INTO jobs VALUES(?,?,?,?)", (
                    str(job.id), str(session_id), job.status,
                    job.model_dump_json(exclude={"error_message"}),
                ))
            except sqlite3.IntegrityError:
                raise BusySessionError("该会话已有任务，请等待完成后再操作。") from None
            return job

    def get_job(self, job_id: Id) -> Job:
        with self._connection() as conn:
            return self._load(conn, "jobs", job_id, Job)

    def transition_job(
        self, job_id: Id, status: JobStatus, *, stage: Stage | None = None,
        error_code: ErrorCode | None = None,
    ) -> Job:
        with self._connection(write=True) as conn:
            job = self._load(conn, "jobs", job_id, Job)
            allowed = {"queued": {"running", "failed"},
                       "running": {"running", "succeeded", "failed"}}
            if status not in allowed.get(job.status, set()):
                raise StateConflictError("任务状态不能回退或重复结束。")
            if (status == "failed") != (error_code is not None):
                raise StateConflictError("失败任务需要错误分类，其他任务不能携带错误。")
            next_stage = (
                "done" if status == "succeeded" else "failed" if status == "failed" else stage
            )
            updated = Job.model_validate({
                **job.model_dump(exclude={"error_message"}), "status": status,
                "stage": next_stage or job.stage, "error_code": error_code, "updated_at": utc_now(),
            })
            conn.execute("UPDATE jobs SET status=?,payload=? WHERE id=?", (
                status, updated.model_dump_json(exclude={"error_message"}), str(job_id),
            ))
            return updated

    def recover_interrupted_jobs(self) -> int:
        """仅服务启动时调用；普通 Store 实例创建不能中断其他实例的活动任务。"""
        with self._connection(write=True) as conn:
            rows = conn.execute("SELECT payload FROM jobs WHERE status IN ('queued','running')")
            count = 0
            for row in rows.fetchall():
                job = Job.model_validate_json(row["payload"])
                recovered = Job.model_validate({
                    **job.model_dump(exclude={"error_message"}), "status": "failed",
                    "stage": "failed", "error_code": "interrupted", "updated_at": utc_now(),
                })
                conn.execute("UPDATE jobs SET status='failed',payload=? WHERE id=?", (
                    recovered.model_dump_json(exclude={"error_message"}), str(job.id),
                ))
                count += 1
            return count

    def save_artifact(self, artifact: Artifact) -> None:
        with self._connection(write=True) as conn:
            revision = self._load(conn, "revisions", artifact.revision_id, Revision)
            if artifact.job_id is not None:
                job = self._load(conn, "jobs", artifact.job_id, Job)
                if job.session_id != revision.session_id or job.revision_id != revision.id:
                    raise StateConflictError("产物必须关联其制作任务的脚本版本。")
            try:
                conn.execute("INSERT INTO artifacts VALUES(?,?,?)", (
                    str(artifact.id), str(artifact.revision_id), artifact.model_dump_json(),
                ))
            except sqlite3.IntegrityError:
                raise StateConflictError("产物索引已存在，不能覆盖。") from None

    def get_artifact(self, artifact_id: Id) -> Artifact:
        with self._connection() as conn:
            return self._load(conn, "artifacts", artifact_id, Artifact)

    def publish_audio(self, session_id: Id, revision_id: Id, artifact_id: Id) -> None:
        with self._connection(write=True) as conn:
            session = self._session_row(conn, session_id)
            revision = self._load(conn, "revisions", revision_id, Revision)
            artifact = self._load(conn, "artifacts", artifact_id, Artifact)
            if (str(revision.session_id) != str(session_id)
                    or session["current_revision_id"] != str(revision_id)
                    or artifact.revision_id != revision.id or artifact.kind != "audio"):
                raise StateConflictError("音频必须对应本会话当前脚本版本。")
            if not artifact.path.is_file() or artifact.path.stat().st_size == 0:
                raise StateConflictError("音频文件不存在或未完成。")
            assert artifact.audio_settings is not None
            selected = ProgramSettings.model_validate_json(session["settings_json"])
            selected = selected.model_copy(update={
                "host_voice": artifact.audio_settings.host_voice,
                "guest_voice": artifact.audio_settings.guest_voice,
            })
            conn.execute("UPDATE sessions SET published_audio_id=?,settings_json=? WHERE id=?", (
                str(artifact_id), selected.model_dump_json(), str(session_id),
            ))

    def list_artifacts(self, session_id: Id) -> list[Artifact]:
        with self._connection() as conn:
            self._session_row(conn, session_id)
            rows = conn.execute(
                "SELECT a.payload FROM artifacts a JOIN revisions r ON a.revision_id=r.id "
                "WHERE r.session_id=? ORDER BY r.number,a.rowid", (str(session_id),),
            )
            return [Artifact.model_validate_json(row["payload"]) for row in rows]

    def get_session(self, session_id: Id) -> Session:
        with self._connection() as conn:
            row = self._session_row(conn, session_id)
            revision = (self._load(conn, "revisions", row["current_revision_id"], Revision)
                        if row["current_revision_id"] else None)
            audio = (self._load(conn, "artifacts", row["published_audio_id"], Artifact)
                     if row["published_audio_id"] else None)
            active = conn.execute(
                "SELECT payload FROM jobs WHERE session_id=? AND status IN ('queued','running')",
                (str(session_id),),
            ).fetchone()
            messages = conn.execute(
                "SELECT payload FROM messages WHERE session_id=? ORDER BY id", (str(session_id),),
            )
            return Session(
                id=UUID(row["id"]), source_text=row["source_text"],
                settings=ProgramSettings.model_validate_json(row["settings_json"]),
                current_revision=revision, published_audio=audio,
                active_job=Job.model_validate_json(active["payload"]) if active else None,
                messages=[Message.model_validate_json(message["payload"]) for message in messages],
            )
