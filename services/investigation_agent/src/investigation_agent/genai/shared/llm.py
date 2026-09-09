"""Shared Bedrock constructors with explicit inputs and no import-time clients."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol, cast


class ChatModel(Protocol):
    def with_structured_output(self, schema: type[Any], **kwargs: Any) -> Any: ...


class EmbeddingModel(Protocol):
    async def aembed_query(self, text: str) -> list[float]: ...


type ChatFactory = Callable[..., ChatModel]
type EmbeddingFactory = Callable[..., EmbeddingModel]


def _default_chat_factory(model_id: str, **options: Any) -> ChatModel:
    from langchain.chat_models import init_chat_model

    return cast(ChatModel, init_chat_model(model_id, model_provider="bedrock_converse", **options))


def _default_embedding_factory(model_id: str, **options: Any) -> EmbeddingModel:
    from langchain_aws import BedrockEmbeddings

    return BedrockEmbeddings(model_id=model_id, **options)


def build_chat_model(
    *,
    model_id: str,
    region_name: str,
    timeout_s: float,
    temperature: float,
    reasoning_effort: str,
    callbacks: Sequence[object] = (),
    factory: ChatFactory = _default_chat_factory,
) -> ChatModel:
    """Build one isolated chat client from resolved provider configuration."""

    from botocore.config import Config

    options: dict[str, Any] = {
        "region_name": region_name,
        "config": Config(
            connect_timeout=timeout_s,
            read_timeout=timeout_s,
            retries={"max_attempts": 0},
        ),
    }
    if "openai.gpt-5.6-terra" not in model_id:
        options.update(temperature=temperature, reasoning_effort=reasoning_effort)
    if callbacks:
        options["callbacks"] = list(callbacks)
    return factory(model_id, **options)


def build_embedding_model(
    *,
    model_id: str,
    region_name: str,
    timeout_s: float,
    factory: EmbeddingFactory = _default_embedding_factory,
) -> EmbeddingModel:
    """Build the shared embedding client from resolved provider configuration."""

    from botocore.config import Config

    return factory(
        model_id,
        region_name=region_name,
        normalize=True,
        config=Config(
            connect_timeout=timeout_s,
            read_timeout=timeout_s,
            retries={"max_attempts": 0},
        ),
    )


__all__ = [
    "ChatFactory",
    "ChatModel",
    "EmbeddingFactory",
    "EmbeddingModel",
    "build_chat_model",
    "build_embedding_model",
]
