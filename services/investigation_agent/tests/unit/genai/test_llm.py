from __future__ import annotations

from typing import Any

from investigation_agent.config.settings import Settings
from investigation_agent.genai.shared.llm import ModelPurpose, build_model_clients


def _settings() -> Settings:
    return Settings(
        ENVIRONMENT_NAME="local",
        INVESTIGATION_AGENT_HOST="127.0.0.1",
        INVESTIGATION_AGENT_PORT=8080,
        AWS_REGION="eu-west-1",
        BEDROCK_CHAT_MODEL_ID="chat-model",
        BEDROCK_EMBEDDING_MODEL_ID="embedding-model",
        EXPECTED_AGENT_INITIALIZER_VERSION="v1",
    )


def test_llm_factories_receive_validated_role_specific_configuration() -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def chat(model_id: str, **options: Any) -> object:
        calls.append(("chat", model_id, options))
        return object()

    def embeddings(model_id: str, **options: Any) -> object:
        calls.append(("embeddings", model_id, options))
        return object()

    clients = build_model_clients(
        _settings(),
        chat_factory=chat,  # type: ignore[arg-type]
        embedding_factory=embeddings,  # type: ignore[arg-type]
        purpose_options={ModelPurpose.GUARDRAIL: {"temperature": 0}},
    )

    assert len([call for call in calls if call[0] == "chat"]) == 7
    assert calls[-1][0:2] == ("embeddings", "embedding-model")
    assert clients.planner is not clients.guardrail
    assert calls[0][2]["region_name"] == "eu-west-1"
    assert calls[1][2]["temperature"] == 0


def test_terra_clients_omit_unsupported_generation_options() -> None:
    calls: list[dict[str, Any]] = []
    settings = _settings().model_copy(
        update={"bedrock_chat_model_id": "global.openai.gpt-5.6-terra"}
    )

    def chat(_model_id: str, **options: Any) -> object:
        calls.append(options)
        return object()

    build_model_clients(
        settings,
        chat_factory=chat,  # type: ignore[arg-type]
        embedding_factory=lambda *_args, **_kwargs: object(),  # type: ignore[arg-type]
    )

    assert calls
    assert all("temperature" not in options for options in calls)
    assert all("reasoning_effort" not in options for options in calls)
