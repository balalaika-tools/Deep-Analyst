"""Shared marker for the trusted evidence-reader adapter.

Each evidence capability owns a narrow structural Protocol over its own domain schemas. Keeping
this composition marker method-free avoids making the central port depend on GenAI schemas or SQL
types; the concrete PostgreSQL adapter implements the capability Protocols used by search, guarded
record queries, source resolution, and graph traversal.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EvidenceReader(Protocol):
    """Marker implemented by the single composed evidence-reader adapter."""


__all__ = ["EvidenceReader"]
