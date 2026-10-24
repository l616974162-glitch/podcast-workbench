"""完整后端契约测试：隔离供应商网络，实际运行 LangGraph、SQLite 和 FFmpeg。"""

import asyncio
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from test_api import wait_job
from test_audio import wav_bytes
from test_service import make_service

from podcast_workbench.api import create_app
from podcast_workbench.service import create_service


def test_all_backend_components_work_together_without_frontend(tmp_path: Path) -> None:
    config = make_service(tmp_path)[0].config
    calls = {"llm": 0, "tts": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.deepseek.com":
            calls["llm"] += 1
            if calls["llm"] == 1:
                result = {"key_points": ["原文事实"], "outline": ["节目提纲"]}
            else:
                result = {
                    "title": "真实组件测试",
                    "summary": "测试简介",
                    "turns": [
                        {"role": "host", "text": "介绍一下？"},
                        {"role": "guest", "text": "解释原文。"},
                    ],
                    "review_summary": "已检查原文一致性",
                }
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(result)}}
                    ]
                },
            )
        if request.method == "POST":
            calls["tts"] += 1
            return httpx.Response(
                200,
                json={
                    "output": {
                        "audio": {"url": "https://result.oss-cn-beijing.aliyuncs.com/audio.wav"}
                    }
                },
            )
        return httpx.Response(200, content=wav_bytes())

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    service = create_service(config, http)
    with TestClient(create_app(config, service)) as client:
        response = client.post("/chat", json={"action": "create", "source_text": "原文事实。"})
        submitted = response.json()
        result = wait_job(client, submitted["job_id"])
        assert result["status"] == "succeeded"
        assert calls == {"llm": 3, "tts": 2}
        assert {item["kind"] for item in result["artifacts"]} == {"audio", "script"}
        assert all("path" not in item for item in result["artifacts"])
        audio = next(item for item in result["artifacts"] if item["kind"] == "audio")
        assert client.get(audio["url"]).content.startswith(b"RIFF")
        events = [
            json.loads(line) for line in (config.app.log_dir / "agent.log").read_text().splitlines()
        ]
        assert all(event["trace_id"] == submitted["trace_id"] for event in events)
        assert len([event for event in events if event["event"] == "llm.end"]) == 3
        assert len([event for event in events if event["event"] == "tool.end"]) == 2
    asyncio.run(http.aclose())


def test_nested_langgraph_automatically_runs_four_agents_and_both_tools(tmp_path: Path) -> None:
    import base64
    from io import BytesIO

    from PIL import Image
    from pydantic import SecretStr

    config = make_service(tmp_path)[0].config
    config.pipeline.auto_cover = True
    config.image.api_key = SecretStr("test-agnes")
    calls = {"llm": 0, "tts": 0, "image": 0}
    picture = BytesIO()
    Image.new("RGB", (1024, 1024), "#7052a3").save(picture, format="PNG")

    def handler(request):
        if request.url.host == "api.deepseek.com":
            calls["llm"] += 1
            payload = json.loads(request.content)
            if calls["llm"] == 1:
                result = {"key_points": ["原文事实"], "outline": ["提纲"]}
            elif calls["llm"] == 4:
                shared = json.loads(payload["messages"][1]["content"])
                assert shared["script"]["title"] == "审校最终稿"
                result = {"prompt": "A square book cover, no text.", "rationale": "依据主题"}
            else:
                result = {"title": "初稿" if calls["llm"] == 2 else "审校最终稿",
                          "summary": "简介", "turns": [
                              {"role": "host", "text": "问题？"},
                              {"role": "guest", "text": "回答。"},
                          ]}
            return httpx.Response(200, json={"choices": [{
                "finish_reason": "stop", "message": {"content": json.dumps(result)},
            }]})
        if request.url.host == "apihub.agnes-ai.com":
            calls["image"] += 1
            return httpx.Response(200, json={"data": [{
                "b64_json": base64.b64encode(picture.getvalue()).decode(),
            }]})
        if request.method == "POST":
            calls["tts"] += 1
            return httpx.Response(200, json={"output": {"audio": {
                "url": "https://result.oss-cn-beijing.aliyuncs.com/audio.wav",
            }}})
        return httpx.Response(200, content=wav_bytes())

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = create_service(config, http)
    with TestClient(create_app(config, service)) as client:
        accepted = client.post("/chat", json={"action": "create", "source_text": "原文事实"}).json()
        result = wait_job(client, accepted["job_id"])
        assert result["status"] == "succeeded" and result["partial"] is False
        assert calls == {"llm": 4, "tts": 2, "image": 1}
        assert {a["kind"] for a in result["artifacts"]} == {"script", "audio", "image"}
        assert all(result["node_states"][node] == "succeeded" for node in (
            "researcher", "writer", "reviewer", "tts", "merge", "cover_planner", "image",
        ))
        events = [json.loads(line) for line in
                  (config.app.log_dir / "agent.log").read_text().splitlines()]
        assert {event["trace_id"] for event in events} == {accepted["trace_id"]}
        assert sum(event["event"] == "llm.end" for event in events) == 4
    asyncio.run(http.aclose())
