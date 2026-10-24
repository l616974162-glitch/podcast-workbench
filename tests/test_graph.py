import asyncio
import json
from pathlib import Path

import httpx
from test_llm import ROOT, context

from podcast_workbench.config import load_config
from podcast_workbench.graph import PodcastGraph
from podcast_workbench.llm import DeepSeekClient


def test_three_nodes_run_in_order_and_reviewer_wins(tmp_path: Path) -> None:
    calls = []
    draft = {"title": "初稿", "summary": "简介", "turns": [
        {"role": "host", "text": "问题？"}, {"role": "guest", "text": "回答。"}
    ]}
    outputs = [{"key_points": ["原文事实"], "outline": ["主题"]}, draft,
               {**draft, "title": "审校后", "review_summary": "已核对材料"}]

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(outputs[len(calls) - 1])
        }}]})

    async def run():
        config = load_config(ROOT / "config.yaml")
        ctx = context(tmp_path)
        stages = []
        ctx.on_stage = stages.append
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            script = await PodcastGraph(config, DeepSeekClient(config.llm, http)).generate(
                {"source_text": "原始材料"}, ctx
            )
        assert stages == ["researcher", "writer", "reviewer"]
        return script

    result = asyncio.run(run())
    assert result.title == "审校后" and len(calls) == 3
    assert "原文事实" in calls[1]["messages"][1]["content"]
    assert "初稿" in calls[2]["messages"][1]["content"]
    events = [json.loads(line)["event"]
              for line in (tmp_path / "agent.log").read_text().splitlines()]
    assert events.count("node.end") == 3 and events.count("llm.end") == 3
