from pathlib import Path

import pytest
from investigation_agent.config.settings import (
    CONFIG_DIR_ENV,
    Settings,
    SettingsError,
    load_settings,
)

REQUIRED_ENV = {
    "ENVIRONMENT_NAME": "local",
    "INVESTIGATION_AGENT_HOST": "127.0.0.1",
    "INVESTIGATION_AGENT_PORT": "8080",
    "AWS_REGION": "eu-central-1",
    "BEDROCK_CHAT_MODEL_ID": "example.chat-v1:0",
    "BEDROCK_EMBEDDING_MODEL_ID": "example.embedding-v1:0",
    "EXPECTED_AGENT_INITIALIZER_VERSION": "agent-runtime@2",
}


@pytest.fixture
def required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setitem(Settings.model_config, "env_file", None)


def test_precedence_is_constructor_then_environment_then_dotenv_yaml_and_default(
    required_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "local.yaml").write_text("retrieval_top_k: 11\n")
    dotenv = tmp_path / "service.env"
    dotenv.write_text("RETRIEVAL_TOP_K=12\n")
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    monkeypatch.setitem(Settings.model_config, "env_file", dotenv)

    assert Settings().retrieval_top_k == 12

    monkeypatch.setenv("RETRIEVAL_TOP_K", "13")
    assert Settings().retrieval_top_k == 13
    assert Settings(RETRIEVAL_TOP_K=14).retrieval_top_k == 14

    monkeypatch.delenv("RETRIEVAL_TOP_K")
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    assert Settings().retrieval_top_k == 11

    (tmp_path / "local.yaml").write_text("{}\n")
    assert Settings().retrieval_top_k == 20


@pytest.mark.parametrize(
    ("name", "value", "expected_field"),
    [
        pytest.param("READER_POOL_MIN_SIZE", "6", "READER_POOL_MIN_SIZE", id="pool-order"),
        pytest.param("MODEL_TIMEOUT_S", "121", "MODEL_TIMEOUT_S", id="deadline-order"),
        pytest.param("RETRY_INITIAL_DELAY_S", "5", "RETRY_INITIAL_DELAY_S", id="retry-order"),
        pytest.param("CLOSURE_MODEL_CALLS", "20", "CLOSURE_MODEL_CALLS", id="reserve"),
        pytest.param("NESTED_TOOL_CALL_LIMIT", "4", "NESTED_TOOL_CALL_LIMIT", id="nested-tools"),
        pytest.param("RETRIEVAL_TOP_K", "257", "RETRIEVAL_TOP_K", id="retrieval-limit"),
        pytest.param("LEXICAL_WEIGHT", "0.8", "LEXICAL_WEIGHT", id="fusion-weight"),
        pytest.param("HISTORY_PAGE_SIZE", "101", "HISTORY_PAGE_SIZE", id="history-bound"),
        pytest.param("SSE_CHUNK_CHARS", "16001", "SSE_CHUNK_CHARS", id="sse-bound"),
        pytest.param("MAX_CONTEXT_TOKENS", "100", "MAX_CONTEXT_TOKENS", id="budget-bound"),
    ],
)
def test_invalid_related_bounds_fail_with_field_names(
    required_env: None,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    expected_field: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(SettingsError, match=expected_field):
        load_settings()


def test_yaml_rejects_unknown_and_secret_keys_without_rendering_values(
    required_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sentinel = "never-render-this-secret"
    (tmp_path / "local.yaml").write_text(
        f"agent_reader_database_url: {sentinel}\nunknown_policy: true\n"
    )
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))

    with pytest.raises(SettingsError) as error:
        load_settings()

    assert "agent_reader_database_url" in str(error.value)
    assert "unknown_policy" in str(error.value)
    assert sentinel not in str(error.value)


def test_committed_policy_defaults(required_env: None) -> None:
    settings = load_settings()

    assert settings.lexical_weight + settings.vector_weight == 1.0
    assert settings.closure_model_calls < settings.main_model_call_limit
    assert settings.nested_tool_call_limit <= 3
