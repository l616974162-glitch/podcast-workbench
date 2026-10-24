"""既有供应商契约回归固定为千问；Edge 用例显式切换引擎。"""

import pytest


@pytest.fixture(autouse=True)
def qwen_contract_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_PROTOCOL", "qwen_tts")
