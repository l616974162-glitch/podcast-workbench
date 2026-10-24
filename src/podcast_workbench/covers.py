"""独立的封面策划 Agent 和图像工具；不重写脚本或配音。"""

from typing import Protocol, TypeVar

from pydantic import BaseModel

from podcast_workbench.config import Settings
from podcast_workbench.errors import ServiceError
from podcast_workbench.models import CoverDraft, CoverOptions, CoverPlan, Revision
from podcast_workbench.observability import RunContext
from podcast_workbench.tools.agnes_image import AgnesImageClient

T = TypeVar("T", bound=BaseModel)


class CoverPlanner(Protocol):
    async def complete(
        self, prompt: str, payload: dict[str, object], schema: type[T],
        ctx: RunContext, node: str,
    ) -> T: ...


class CoverWorkflow:
    def __init__(self, config: Settings, planner: CoverPlanner, image: AgnesImageClient) -> None:
        self.config, self.planner, self.image = config, planner, image

    async def plan(
        self, revision: Revision, options: CoverOptions, ctx: RunContext,
    ) -> CoverPlan:
        ctx.stage("cover_planner")
        ctx.emit("node.start", node="cover_planner")
        try:
            draft = await self.planner.complete(
                self.config.agents.cover_planner.prompt,
                {"script": revision.script.model_dump(mode="json"),
                 "style": options.style, "user_direction": options.prompt},
                CoverDraft, ctx, "cover_planner",
            )
        except ServiceError:
            ctx.emit("node.error", node="cover_planner", status="failed")
            raise
        ctx.emit("node.end", node="cover_planner", status="succeeded")
        return CoverPlan(**draft.model_dump(), style=options.style,
                         revision_id=revision.id, job_id=ctx.job_id)
