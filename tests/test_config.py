from pathlib import Path

import pytest

from podcast_workbench.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.yaml"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL", "QWEN_TTS_URL", "QWEN_TTS_MODEL", "FFMPEG_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


def test_config_without_keys_allows_offline_setup(tmp_path: Path) -> None:
    config = load_config(CONFIG, env_file=tmp_path / "absent.env")
    assert config.llm.model == "deepseek-flash"
    assert config.tts.max_text_chars == 600
    assert list(config.pipeline.nodes) == ["researcher", "writer", "reviewer"]
    assert config.app.output_dir == ROOT / "output"


def test_require_keys_reports_names_without_values(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY.*DASHSCOPE_API_KEY"):
        load_config(CONFIG, env_file=tmp_path / "absent.env", require_secrets=True)


def test_process_environment_wins_and_secrets_are_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=file-secret\nDASHSCOPE_API_KEY=tts-secret\n"
        "DEEPSEEK_MODEL=account-model\n", encoding="utf-8"
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-secret")
    config = load_config(CONFIG, env_file=env_file, require_secrets=True)
    assert config.llm.api_key.get_secret_value() == "process-secret"
    assert config.llm.model == "account-model"
    assert "process-secret" not in repr(config)
    assert "tts-secret" not in config.model_dump_json()


def test_literal_key_rejected_without_echo(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.read_text(encoding="utf-8").replace(
        "${DEEPSEEK_API_KEY}", "do-not-echo-this-key"
    ), encoding="utf-8")
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert "do-not-echo-this-key" not in str(error.value)
    assert "环境变量" in str(error.value)


@pytest.mark.parametrize("value", ["601", "0"])
def test_tts_length_respects_provider_limit(tmp_path: Path, value: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.read_text(encoding="utf-8").replace(
        "max_text_chars: 600", f"max_text_chars: {value}"
    ), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_invalid_yaml_does_not_echo_source(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("llm: [secret-that-must-stay-private", encoding="utf-8")
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert "secret-that-must-stay-private" not in str(error.value)


def test_unknown_reference_is_reported_safely(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.read_text(encoding="utf-8").replace(
        "${DEEPSEEK_MODEL}", "${MISSING_MODEL}"
    ), encoding="utf-8")
    with pytest.raises(ConfigError, match="MISSING_MODEL"):
        load_config(path)
