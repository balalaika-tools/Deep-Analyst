"""Relationship extraction shares the entity task's model construction."""

from __future__ import annotations

from ingestion.genai.entity_extraction.llm import build_chat_model

__all__ = ["build_chat_model"]
