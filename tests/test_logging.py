import json
from pathlib import Path
from uuid import uuid4

import pytest

from podcast_workbench.observability import EventLogger


def test_trace_is_preserved_without_logging_raw_secrets_or_signed_urls(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "agent.log"
    logger = EventLogger(path)
    trace_id, session_id = uuid4(), uuid4()
    raw_secret = "test-secret-that-must-not-be-logged"
    signed_url = "https://example.invalid/audio.wav?Signature=private-signature"
    logger.emit({
        "event": "run.start", "trace_id": str(trace_id), "session_id": str(session_id),
        "api_key": raw_secret, "headers": {"Authorization": raw_secret},
    })
    logger.emit({
        "event": "llm.end", "trace_id": str(trace_id), "session_id": str(session_id),
        "node": "writer", "provider": "deepseek", "duration_ms": 23,
        "status": "succeeded", "response": signed_url, "exception": raw_secret,
    })
    content = path.read_text(encoding="utf-8")
    assert raw_secret not in content and signed_url not in content
    records = [json.loads(line) for line in content.splitlines()]
    assert len(records) == 2
    assert all(row["trace_id"] == str(trace_id) for row in records)
    assert records[1]["duration_ms"] == 23
    assert "timestamp" in records[0]


def test_secret_cannot_be_smuggled_in_known_fields(tmp_path: Path) -> None:
    path = tmp_path / "agent.log"
    logger = EventLogger(path)
    with pytest.raises(ValueError) as error:
        logger.emit({
            "event": "llm.end", "trace_id": str(uuid4()), "session_id": str(uuid4()),
            "node": "secret-disguised-as-node",
        })
    assert "secret-disguised-as-node" not in str(error.value)
    assert not path.exists()


def test_log_contains_only_safe_error_classification(tmp_path: Path) -> None:
    path = tmp_path / "agent.log"
    EventLogger(path).emit({
        "event": "tool.error", "trace_id": str(uuid4()), "session_id": str(uuid4()),
        "job_id": str(uuid4()), "revision_id": str(uuid4()), "node": "tts",
        "status": "failed", "error_code": "upstream_error",
        "error_message": "RAW PROVIDER RESPONSE MUST NOT BE SAVED",
    })
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["error_code"] == "upstream_error"
    assert "error_message" not in record
