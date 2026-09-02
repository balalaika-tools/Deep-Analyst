from __future__ import annotations

from typing import Any

import pytest
from investigation_agent.bootstrap import app as bootstrap_app
from investigation_agent.config.secrets import ServingSecrets
from investigation_agent.config.settings import Settings


def test_sub_second_shutdown_budget_keeps_uvicorn_graceful_shutdown_non_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uvicorn

    captured: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    settings = Settings(
        ENVIRONMENT_NAME="local",
        INVESTIGATION_AGENT_HOST="127.0.0.1",
        INVESTIGATION_AGENT_PORT=8080,
        AWS_REGION="eu-west-1",
        BEDROCK_CHAT_MODEL_ID="chat-model",
        BEDROCK_EMBEDDING_MODEL_ID="embedding-model",
        EXPECTED_AGENT_INITIALIZER_VERSION="agent-runtime@2",
        SHUTDOWN_TIMEOUT_S=0.5,
    )
    secrets = ServingSecrets(
        AGENT_READER_DATABASE_URL="postgresql://agent_reader:reader-secret@db:5432/app",
        AGENT_WRITER_DATABASE_URL="postgresql://agent_writer:writer-secret@db:5432/app",
    )

    assert bootstrap_app.run_serving(settings, secrets) == 0
    assert captured["timeout_graceful_shutdown"] == 1
