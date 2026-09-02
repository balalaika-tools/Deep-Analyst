from pathlib import Path

import pytest
from investigation_agent.config.secrets import (
    InitializerSecrets,
    SecretsError,
    ServingSecrets,
    load_initializer_secrets,
    load_serving_secrets,
)

SECRET_ENV_NAMES = {
    "AGENT_READER_DATABASE_URL",
    "AGENT_WRITER_DATABASE_URL",
    "AGENT_OWNER_DATABASE_URL",
    "AGENT_READER_PASSWORD",
    "AGENT_WRITER_PASSWORD",
}


@pytest.fixture
def isolated_secret_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ServingSecrets.model_config, "env_file", None)
    monkeypatch.setitem(InitializerSecrets.model_config, "env_file", None)
    for name in SECRET_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_serving_loader_reads_only_reader_and_writer_dsns(
    isolated_secret_sources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "AGENT_READER_DATABASE_URL", "postgresql://agent_reader:reader-secret@db:5432/app"
    )
    monkeypatch.setenv(
        "AGENT_WRITER_DATABASE_URL", "postgresql://agent_writer:writer-secret@db:5432/app"
    )

    secrets = load_serving_secrets()

    assert set(type(secrets).model_fields) == {"reader_database_url", "writer_database_url"}
    assert not hasattr(secrets, "owner_database_url")


def test_initializer_loader_reads_only_owner_dsn_and_role_passwords(
    isolated_secret_sources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_OWNER_DATABASE_URL", "postgresql://app:owner-secret@db:5432/app")
    monkeypatch.setenv("AGENT_READER_PASSWORD", "reader-password-123")
    monkeypatch.setenv("AGENT_WRITER_PASSWORD", "writer-password-123")

    secrets = load_initializer_secrets()

    assert set(type(secrets).model_fields) == {
        "owner_database_url",
        "reader_password",
        "writer_password",
    }
    assert not hasattr(secrets, "reader_database_url")


@pytest.mark.parametrize(
    ("loader", "field", "secret_value"),
    [
        pytest.param(
            load_serving_secrets,
            "AGENT_READER_DATABASE_URL",
            "sentinel-reader-value",
            id="serving-dsn",
        ),
        pytest.param(
            load_initializer_secrets,
            "AGENT_READER_PASSWORD",
            "tiny-secret",
            id="initializer-password",
        ),
    ],
)
def test_secret_validation_names_fields_without_values(
    isolated_secret_sources: None,
    monkeypatch: pytest.MonkeyPatch,
    loader: object,
    field: str,
    secret_value: str,
) -> None:
    if loader is load_serving_secrets:
        monkeypatch.setenv(field, secret_value)
        monkeypatch.setenv(
            "AGENT_WRITER_DATABASE_URL", "postgresql://agent_writer:writer-secret@db:5432/app"
        )
    else:
        monkeypatch.setenv("AGENT_OWNER_DATABASE_URL", "postgresql://app:owner-secret@db:5432/app")
        monkeypatch.setenv(field, secret_value)
        monkeypatch.setenv("AGENT_WRITER_PASSWORD", "writer-password-123")

    with pytest.raises(SecretsError) as error:
        loader()  # type: ignore[operator]

    assert field in str(error.value)
    assert secret_value not in str(error.value)


@pytest.mark.parametrize(
    ("loader", "unexpected_name"),
    [
        pytest.param(load_serving_secrets, "AGENT_OWNER_DATABASE_URL", id="owner-in-serving"),
        pytest.param(
            load_initializer_secrets,
            "AGENT_READER_DATABASE_URL",
            id="serving-dsn-in-initializer",
        ),
        pytest.param(load_serving_secrets, "AGENT_UNKNOWN_SECRET", id="unknown-secret"),
    ],
)
def test_loader_rejects_unknown_or_misplaced_secret_fields(
    isolated_secret_sources: None,
    monkeypatch: pytest.MonkeyPatch,
    loader: object,
    unexpected_name: str,
) -> None:
    sentinel = "misplaced-private-value"
    monkeypatch.setenv(unexpected_name, sentinel)

    with pytest.raises(SecretsError) as error:
        loader()  # type: ignore[operator]

    assert unexpected_name in str(error.value)
    assert sentinel not in str(error.value)


def test_dotenv_with_non_secret_settings_alongside_secrets_loads(
    isolated_secret_sources: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "ENVIRONMENT_NAME=dev\n"
        "AWS_REGION=eu-west-1\n"
        "AGENT_READER_DATABASE_URL=postgresql://agent_reader:reader-secret@db:5432/app\n"
        "AGENT_WRITER_DATABASE_URL=postgresql://agent_writer:writer-secret@db:5432/app\n"
    )
    monkeypatch.setitem(ServingSecrets.model_config, "env_file", dotenv)

    secrets = load_serving_secrets()

    assert secrets.reader_database_url.get_secret_value().startswith("postgresql://agent_reader:")


def test_dotenv_with_misplaced_secret_is_still_rejected(
    isolated_secret_sources: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "ENVIRONMENT_NAME=dev\n"
        "AGENT_READER_DATABASE_URL=postgresql://agent_reader:reader-secret@db:5432/app\n"
        "AGENT_WRITER_DATABASE_URL=postgresql://agent_writer:writer-secret@db:5432/app\n"
        "AGENT_OWNER_DATABASE_URL=postgresql://app:owner-private-value@db:5432/app\n"
    )
    monkeypatch.setitem(ServingSecrets.model_config, "env_file", dotenv)

    with pytest.raises(SecretsError) as error:
        load_serving_secrets()

    assert "AGENT_OWNER_DATABASE_URL" in str(error.value)
    assert "ENVIRONMENT_NAME" not in str(error.value)
    assert "owner-private-value" not in str(error.value)
