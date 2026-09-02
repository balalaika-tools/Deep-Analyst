"""Embedding model construction only."""

from __future__ import annotations

from botocore.config import Config
from langchain_aws import BedrockEmbeddings

from ingestion.config.settings import Settings


def build_embeddings(settings: Settings) -> BedrockEmbeddings:
    return BedrockEmbeddings(
        model_id=settings.bedrock_embedding_model_id,
        region_name=settings.aws_region,
        dimensions=settings.embedding_dimensions,
        normalize=True,
        config=Config(max_pool_connections=settings.llm_max_in_flight),
    )
