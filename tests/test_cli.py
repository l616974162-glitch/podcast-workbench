import sys
from pathlib import Path

import pytest

from podcast_workbench.__main__ import main

ROOT = Path(__file__).resolve().parents[1]


def test_doctor_is_offline_and_reports_missing_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    config = tmp_path / "config.yaml"
    config.write_text((ROOT / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["podcast-workbench", "doctor", "--config", str(config)])
    assert main() == 0
    output = capsys.readouterr().out
    assert "DEEPSEEK_API_KEY" in output
    assert "FFmpeg" in output
    assert "不发起网络请求" in output


def test_check_config_can_require_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    config = tmp_path / "config.yaml"
    config.write_text((ROOT / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["podcast-workbench", "check-config", "--require-secrets",
                                      "--config", str(config)])
    assert main() == 1
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err
