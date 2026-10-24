"""LangGraph 固定三 Agent 流水线，每次运行独立上下文。"""

from typing import NotRequired, TypedDict, TypeVar

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from podcast_workbench.config import Settings
from podcast_workbench.errors import ServiceError
from podcast_workbench.llm import DeepSeekClient
from podcast_workbench.models import Analysis, Script, Stage
from podcast_workbench.observability import RunContext

T = TypeVar("T", bound=BaseModel)


class GraphState(TypedDict):
    request: dict[str, object]
    analysis: NotRequired[dict[str, object]]
    script: NotRequired[dict[str, object]]


class PodcastGraph:
    def __init__(self, config: Settings, client: DeepSeekClient) -> None:
        self.config = config
        self.client = client

    async def generate(self, payload: dict[str, object], ctx: RunContext) -> Script:
        async def call(node: Stage, data: dict[str, object], schema: type[T]) -> T:
            ctx.stage(node)
            ctx.emit("node.start", node=node)
            try:
                prompt = getattr(self.config.agents, node).prompt
                result = await self.client.complete(prompt, data, schema, ctx, node)
            except ServiceError as error:
                ctx.emit("node.error", node=node, status="failed", error_code=error.code)
                raise
            ctx.emit("node.end", node=node, status="succeeded")
            return result

        async def researcher(state: GraphState) -> dict[str, object]:
            result = await call("researcher", state["request"], Analysis)
            return {"analysis": result.model_dump(mode="json")}

        async def writer(state: GraphState) -> dict[str, object]:
            result = await call(
                "writer", {**state["request"], "analysis": state["analysis"]}, Script,
            )
            return {"script": result.model_dump(mode="json")}

        async def reviewer(state: GraphState) -> dict[str, object]:
            result = await call("reviewer", {
                **state["request"], "analysis": state["analysis"], "draft": state["script"],
            }, Script)
            return {"script": result.model_dump(mode="json")}

        graph = StateGraph(GraphState)
        nodes = {"researcher": researcher, "writer": writer, "reviewer": reviewer}
        previous = START
        for name in self.config.pipeline.nodes:
            graph.add_node(name, nodes[name])
            graph.add_edge(previous, name)
            previous = name
        graph.add_edge(previous, END)
        result = await graph.compile().ainvoke(
            {"request": payload}, {"recursion_limit": self.config.pipeline.recursion_limit},
        )
        script = Script.model_validate(result["script"])
        analysis = Analysis.model_validate(result["analysis"])
        return script.model_copy(update={"outline": analysis.outline})
