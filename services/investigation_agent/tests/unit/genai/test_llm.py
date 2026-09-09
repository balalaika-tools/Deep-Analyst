from __future__ import annotations

from typing import Any

from investigation_agent.genai.shared.llm import build_chat_model, build_embedding_model


def test_chat_constructor_receives_only_resolved_provider_configuration() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def chat(model_id: str, **options: Any) -> object:
        calls.append((model_id, options))
        return object()

    result = build_chat_model(
        model_id="chat-model",
        region_name="eu-west-1",
        timeout_s=45,
        temperature=0.2,
        reasoning_effort="medium",
        callbacks=("callback",),
        factory=chat,  # type: ignore[arg-type]
    )

    assert result is not None
    assert calls[0][0] == "chat-model"
    assert calls[0][1]["region_name"] == "eu-west-1"
    assert calls[0][1]["temperature"] == 0.2
    assert calls[0][1]["reasoning_effort"] == "medium"
    assert calls[0][1]["callbacks"] == ["callback"]


def test_terra_constructor_omits_unsupported_generation_options() -> None:
    calls: list[dict[str, Any]] = []

    def chat(_model_id: str, **options: Any) -> object:
        calls.append(options)
        return object()

    build_chat_model(
        model_id="global.openai.gpt-5.6-terra",
        region_name="eu-west-1",
        timeout_s=45,
        temperature=0.2,
        reasoning_effort="medium",
        factory=chat,  # type: ignore[arg-type]
    )

    assert "temperature" not in calls[0]
    assert "reasoning_effort" not in calls[0]


def test_embedding_constructor_owns_embedding_specific_options() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def embeddings(model_id: str, **options: Any) -> object:
        calls.append((model_id, options))
        return object()

    result = build_embedding_model(
        model_id="embedding-model",
        region_name="eu-west-1",
        timeout_s=45,
        factory=embeddings,  # type: ignore[arg-type]
    )

    assert result is not None
    assert calls[0][0] == "embedding-model"
    assert calls[0][1]["region_name"] == "eu-west-1"
    assert calls[0][1]["normalize"] is True
