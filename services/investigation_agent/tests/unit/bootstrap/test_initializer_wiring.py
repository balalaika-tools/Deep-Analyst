from __future__ import annotations

from pathlib import Path

import pytest
from investigation_agent.bootstrap.app import run_initializer
from investigation_agent.config.secrets import InitializerSecrets
from investigation_agent.config.settings import Settings
from pydantic import SecretStr


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        ENVIRONMENT_NAME="local",
        INVESTIGATION_AGENT_HOST="127.0.0.1",
        INVESTIGATION_AGENT_PORT=8080,
        AWS_REGION="eu-west-1",
        BEDROCK_CHAT_MODEL_ID="chat-model",
        BEDROCK_EMBEDDING_MODEL_ID="embedding-model",
        EXPECTED_AGENT_INITIALIZER_VERSION="agent-runtime@2",
        AUTHORIZATION_ADAPTER="development",
        DEVELOPMENT_OWNER_ID="owner",
        _env_file=None,
        _yaml_file=tmp_path / "missing.yaml",
    )


def test_initializer_bootstrap_passes_only_validated_owner_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, str] = {}

    async def fake_initialize_database(**kwargs: str) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        "investigation_agent.adapters.postgres.initializer.initialize_database",
        fake_initialize_database,
    )
    secrets = InitializerSecrets(
        AGENT_OWNER_DATABASE_URL=SecretStr("postgresql://owner:password@db/app"),
        AGENT_READER_PASSWORD=SecretStr("reader-password-123"),
        AGENT_WRITER_PASSWORD=SecretStr("writer-password-123"),
        _env_file=None,
    )

    assert run_initializer(_settings(tmp_path), secrets) == 0
    assert captured == {
        "owner_dsn": "postgresql://owner:password@db/app",
        "reader_password": "reader-password-123",
        "writer_password": "writer-password-123",
        "expected_version": "agent-runtime@2",
    }
