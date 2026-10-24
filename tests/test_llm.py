import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from podcast_workbench.config import load_config
from podcast_workbench.errors import ServiceError
from podcast_workbench.llm import DeepSeekClient
from podcast_workbench.models import Analysis
from podcast_workbench.observability import EventLogger, RunContext

ROOT = Path(__file__).resolve().parents[1]


def context(tmp_path: Path) -> RunContext:
    return RunContext(uuid4(), uuid4(), uuid4(), EventLogger(tmp_path / "agent.log"))


def test_deepseek_protocol_and_json_validation(tmp_path: Path) -> None:
    config = load_config(ROOT / "config.yaml").llm.model_copy(
        update={"api_key": SecretStr("test-only-key")}
    )

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-only-key"
        assert body["response_format"] == {"type": "json_object"}
        assert body["thinking"] == {"type": "disabled"}
        assert "JSON" in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"key_points": ["事实"], "outline": ["讨论"]})
                        },
                    }
                ]
            },
        )

    async def run() -> Analysis:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            return await DeepSeekClient(config, http).complete(
                "整理材料", {"source_text": "原文"}, Analysis, context(tmp_path), "researcher"
            )

    assert asyncio.run(run()).key_points == ["事实"]


def test_invalid_json_is_bounded_to_two_attempts(tmp_path: Path) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "length", "message": {"content": "private invalid output"}}
                ]
            },
        )

    async def run() -> None:
        config = load_config(ROOT / "config.yaml").llm
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            await DeepSeekClient(config, http).complete(
                "整理", {}, Analysis, context(tmp_path), "researcher"
            )

    with pytest.raises(ServiceError) as error:
        asyncio.run(run())
    assert error.value.code == "invalid_output"
    assert len(calls) == 2
    assert "private invalid output" not in (tmp_path / "agent.log").read_text()


def test_http_failure_is_safe_and_not_automatically_retried(tmp_path: Path) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, text="Bearer secret-from-upstream")

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            await DeepSeekClient(load_config(ROOT / "config.yaml").llm, http).complete(
                "整理", {}, Analysis, context(tmp_path), "researcher"
            )

    with pytest.raises(ServiceError) as error:
        asyncio.run(run())
    assert error.value.code == "upstream_error" and len(calls) == 1
    assert "secret-from-upstream" not in str(error.value)


@pytest.mark.parametrize("choices", [[None], [], [{"message": None}], "invalid"])
def test_malformed_provider_shape_is_reported_as_invalid_output(tmp_path: Path, choices) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": choices})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            await DeepSeekClient(load_config(ROOT / "config.yaml").llm, http).complete(
                "整理",
                {},
                Analysis,
                context(tmp_path),
                "researcher",
            )

    with pytest.raises(ServiceError) as error:
        asyncio.run(run())
    assert error.value.code == "invalid_output"
