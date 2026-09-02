from collections.abc import Callable
from typing import Any, cast

import pytest
from ingestion.config.settings import Settings
from ingestion.genai.embeddings.llm import build_embeddings
from ingestion.genai.entity_extraction.llm import build_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.rate_limiters import BaseRateLimiter


def test_chat_model_receives_configured_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
    scripted_model_factory: Callable[[], BaseChatModel],
) -> None:
    captured: dict[str, Any] = {}
    model = scripted_model_factory()

    def fake_init_chat_model(model_id: str, **kwargs: Any) -> BaseChatModel:
        captured.update(model_id=model_id, **kwargs)
        return model

    monkeypatch.setattr("langchain.chat_models.init_chat_model", fake_init_chat_model)
    settings = Settings.model_construct(
        bedrock_chat_model_id="example.chat-model-v1:0",
        aws_region="eu-central-1",
        llm_reasoning_effort="high",
        llm_max_in_flight=37,
    )

    result = build_chat_model(
        settings,
        rate_limiter=cast(BaseRateLimiter, object()),
        callbacks=[],
    )

    assert result is model
    assert captured["reasoning_effort"] == "high"
    assert captured["config"].max_pool_connections == 37


def test_embedding_client_pool_matches_the_physical_in_flight_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    handle = object()

    def fake_bedrock_embeddings(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return handle

    monkeypatch.setattr("ingestion.genai.embeddings.llm.BedrockEmbeddings", fake_bedrock_embeddings)
    settings = Settings.model_construct(
        bedrock_embedding_model_id="amazon.titan-embed-text-v2:0",
        aws_region="eu-central-1",
        embedding_dimensions=1024,
        llm_max_in_flight=37,
    )

    result = build_embeddings(settings)

    assert result is handle
    assert captured["config"].max_pool_connections == 37
