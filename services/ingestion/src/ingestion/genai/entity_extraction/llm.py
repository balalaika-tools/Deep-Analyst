"""Chat model construction and binding for extraction tasks."""

from __future__ import annotations

from botocore.config import Config
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.rate_limiters import BaseRateLimiter

from ingestion.config.settings import Settings


def build_chat_model(
    settings: Settings,
    *,
    rate_limiter: BaseRateLimiter,
    callbacks: list[BaseCallbackHandler],
) -> BaseChatModel:
    """Bedrock Converse via `init_chat_model`; the shared limiter throttles every attempt."""
    from langchain.chat_models import init_chat_model

    model = init_chat_model(
        settings.bedrock_chat_model_id,
        model_provider="bedrock_converse",
        region_name=settings.aws_region,
        temperature=0,
        reasoning_effort=settings.llm_reasoning_effort,
        config=Config(max_pool_connections=settings.llm_max_in_flight),
        rate_limiter=rate_limiter,
        callbacks=callbacks,
    )
    assert isinstance(model, BaseChatModel)
    return model
