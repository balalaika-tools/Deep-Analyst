"""Embedding model adapter owned by the evidence-search capability."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from investigation_agent.genai.shared.llm import EmbeddingModel


class BedrockTextEmbedder:
    """Deadline-bounded query embedding over the configured embedding client."""

    def __init__(self, embeddings: EmbeddingModel) -> None:
        self._embeddings = embeddings

    async def embed(self, text: str, *, deadline: float) -> Sequence[float]:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("embedding deadline exhausted")
        async with asyncio.timeout(remaining):
            values = await self._embeddings.aembed_query(text)
        if not isinstance(values, list) or not all(
            isinstance(item, int | float) for item in values
        ):
            raise TypeError("embedding client returned an invalid vector")
        return tuple(float(item) for item in values)


__all__ = ["BedrockTextEmbedder"]
