from pathlib import Path

import investigation_agent.main as entrypoints
import pytest
from investigation_agent.config.secrets import InitializerSecrets, ServingSecrets
from investigation_agent.config.settings import CONFIG_DIR_ENV, Settings

NONSECRET_ENV = {
    "ENVIRONMENT_NAME": "local",
    "INVESTIGATION_AGENT_HOST": "127.0.0.1",
    "INVESTIGATION_AGENT_PORT": "8080",
    "AWS_REGION": "eu-central-1",
    "BEDROCK_CHAT_MODEL_ID": "example.chat-v1:0",
    "BEDROCK_EMBEDDING_MODEL_ID": "example.embedding-v1:0",
    "EXPECTED_AGENT_INITIALIZER_VERSION": "agent-runtime@2",
}

ALL_SECRET_NAMES = {
    "AGENT_READER_DATABASE_URL",
    "AGENT_WRITER_DATABASE_URL",
    "AGENT_OWNER_DATABASE_URL",
    "AGENT_READER_PASSWORD",
    "AGENT_WRITER_PASSWORD",
}


@pytest.fixture
def isolated_entrypoint_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for key, value in NONSECRET_ENV.items():
        monkeypatch.setenv(key, value)
    for name in ALL_SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setitem(ServingSecrets.model_config, "env_file", None)
    monkeypatch.setitem(InitializerSecrets.model_config, "env_file", None)
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))
    (tmp_path / "local.yaml").write_text("{}\n")
    return tmp_path


@pytest.mark.parametrize(
    ("name", "value"),
    [
        pytest.param("AGENT_READER_DATABASE_URL", None, id="missing-secret"),
        pytest.param("AGENT_READER_DATABASE_URL", "private-invalid-dsn", id="invalid-secret"),
    ],
)
def test_serving_validation_failure_prevents_every_external_constructor(
    isolated_entrypoint_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    value: str | None,
) -> None:
    if value is not None:
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(
        "AGENT_WRITER_DATABASE_URL", "postgresql://agent_writer:writer-secret@db:5432/app"
    )
    external_constructor_calls: list[str] = []

    def launch(settings: Settings, secrets: ServingSecrets) -> int:
        del settings, secrets
        external_constructor_calls.append("telemetry/pools/saver/models")
        return 0

    assert entrypoints.main(launcher=launch) == entrypoints.EXIT_CONFIGURATION
    assert external_constructor_calls == []
    assert value is None or value not in capsys.readouterr().err


@pytest.mark.parametrize("yaml_key", ["unknown_policy", "agent_reader_database_url"])
def test_unknown_or_misplaced_yaml_field_prevents_external_construction(
    isolated_entrypoint_env: Path,
    capsys: pytest.CaptureFixture[str],
    yaml_key: str,
) -> None:
    sentinel = "private-yaml-value"
    (isolated_entrypoint_env / "local.yaml").write_text(f"{yaml_key}: {sentinel}\n")
    external_constructor_calls: list[str] = []

    def launch(settings: Settings, secrets: ServingSecrets) -> int:
        del settings, secrets
        external_constructor_calls.append("telemetry/pools/saver/models")
        return 0

    assert entrypoints.main(launcher=launch) == entrypoints.EXIT_CONFIGURATION
    assert external_constructor_calls == []
    assert sentinel not in capsys.readouterr().err


def test_initializer_missing_role_secret_prevents_external_construction(
    isolated_entrypoint_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_OWNER_DATABASE_URL", "postgresql://app:owner-secret@db:5432/app")
    monkeypatch.setenv("AGENT_READER_PASSWORD", "reader-password-123")
    calls: list[str] = []

    def launch(settings: Settings, secrets: InitializerSecrets) -> int:
        del settings, secrets
        calls.append("telemetry/pools/saver/models")
        return 0

    assert entrypoints.initializer_main(launcher=launch) == entrypoints.EXIT_CONFIGURATION
    assert calls == []


def test_each_entrypoint_rejects_the_other_process_secret_subset_without_launch(
    isolated_entrypoint_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "AGENT_READER_DATABASE_URL", "postgresql://agent_reader:reader-secret@db:5432/app"
    )
    monkeypatch.setenv(
        "AGENT_WRITER_DATABASE_URL", "postgresql://agent_writer:writer-secret@db:5432/app"
    )
    monkeypatch.setenv("AGENT_OWNER_DATABASE_URL", "invalid-owner-dsn")
    serving_calls: list[str] = []

    def serve(settings: Settings, secrets: ServingSecrets) -> int:
        del settings, secrets
        serving_calls.append("serve")
        return 0

    assert entrypoints.main(launcher=serve) == entrypoints.EXIT_CONFIGURATION
    assert serving_calls == []

    monkeypatch.delenv("AGENT_READER_DATABASE_URL")
    monkeypatch.delenv("AGENT_WRITER_DATABASE_URL")
    monkeypatch.setenv("AGENT_OWNER_DATABASE_URL", "postgresql://app:owner-secret@db:5432/app")
    monkeypatch.setenv("AGENT_READER_PASSWORD", "reader-password-123")
    monkeypatch.setenv("AGENT_WRITER_PASSWORD", "writer-password-123")
    initializer_calls: list[str] = []

    def initialize(settings: Settings, secrets: InitializerSecrets) -> int:
        del settings, secrets
        initializer_calls.append("initialize")
        return 0

    monkeypatch.setenv("AGENT_READER_DATABASE_URL", "private-serving-dsn")

    assert entrypoints.initializer_main(launcher=initialize) == entrypoints.EXIT_CONFIGURATION
    assert initializer_calls == []
