"""Shared Bedrock client construction with no import-time external effects."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, cast

from investigation_agent.config.settings import Settings


class ChatModel(Protocol):
    def with_structured_output(self, schema: type[Any], **kwargs: Any) -> Any: ...


class EmbeddingModel(Protocol):
    async def aembed_query(self, text: str) -> list[float]: ...


class ModelPurpose(StrEnum):
    PLANNER = "planner"
    GUARDRAIL = "guardrail"
    SEARCH = "search"
    QUERY = "query"
    PROJECTION = "projection"
    VERIFIER = "verifier"
    CLOSURE = "closure"


@dataclass(frozen=True, slots=True)
class ModelClients:
    """Explicit role bindings prevent accidental cross-purpose model reuse."""

    planner: ChatModel
    guardrail: ChatModel
    search: ChatModel
    query: ChatModel
    projection: ChatModel
    verifier: ChatModel
    closure: ChatModel
    embeddings: EmbeddingModel

    def chat(self, purpose: ModelPurpose) -> ChatModel:
        return cast(ChatModel, getattr(self, purpose.value))


type ChatFactory = Callable[..., ChatModel]
type EmbeddingFactory = Callable[..., EmbeddingModel]


def _default_chat_factory(model_id: str, **options: Any) -> ChatModel:
    from langchain.chat_models import init_chat_model

    return cast(ChatModel, init_chat_model(model_id, model_provider="bedrock_converse", **options))


def _default_embedding_factory(model_id: str, **options: Any) -> EmbeddingModel:
    from langchain_aws import BedrockEmbeddings

    return BedrockEmbeddings(model_id=model_id, **options)


def build_model_clients(
    settings: Settings,
    *,
    chat_factory: ChatFactory = _default_chat_factory,
    embedding_factory: EmbeddingFactory = _default_embedding_factory,
    callbacks: Sequence[object] = (),
    purpose_options: Mapping[ModelPurpose, Mapping[str, Any]] | None = None,
) -> ModelClients:
    """Build purpose-specific clients after settings validation."""

    from botocore.config import Config

    sdk_config = Config(
        connect_timeout=settings.model_timeout_s,
        read_timeout=settings.model_timeout_s,
        retries={"max_attempts": 0},
    )
    shared: dict[str, Any] = {
        "region_name": settings.aws_region,
        "temperature": settings.model_temperature,
        "reasoning_effort": settings.model_reasoning_effort,
        "config": sdk_config,
    }
    if callbacks:
        shared["callbacks"] = list(callbacks)
    overrides = purpose_options or {}
    chats = {
        purpose: chat_factory(
            settings.bedrock_chat_model_id,
            **{**shared, **overrides.get(purpose, {})},
        )
        for purpose in ModelPurpose
    }
    embeddings = embedding_factory(
        settings.bedrock_embedding_model_id,
        region_name=settings.aws_region,
        normalize=True,
        config=sdk_config,
    )
    return ModelClients(
        planner=chats[ModelPurpose.PLANNER],
        guardrail=chats[ModelPurpose.GUARDRAIL],
        search=chats[ModelPurpose.SEARCH],
        query=chats[ModelPurpose.QUERY],
        projection=chats[ModelPurpose.PROJECTION],
        verifier=chats[ModelPurpose.VERIFIER],
        closure=chats[ModelPurpose.CLOSURE],
        embeddings=embeddings,
    )


__all__ = [
    "ChatModel",
    "EmbeddingModel",
    "ModelClients",
    "ModelPurpose",
    "build_model_clients",
]
