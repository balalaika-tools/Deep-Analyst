"""Identifier patterns in prose. Rules run before any model and their spans win."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from evidence_model import EntityType

from ingestion.domain.normalization import (
    NormalizationError,
    normalize_email,
    normalize_iban,
    normalize_imei,
    normalize_invoice_ref,
    normalize_phone,
)


@dataclass(frozen=True, slots=True)
class IdentifierSpan:
    entity_type: EntityType
    raw: str
    normalized_key: str
    char_start: int
    char_end: int


# Ordered from most to least specific so a longer identifier claims its characters
# before a shorter pattern (a phone regex must not match inside an IBAN or IMEI).
_RULES: tuple[tuple[EntityType, re.Pattern[str], Callable[[str], str]], ...] = (
    (
        EntityType.FINANCIAL_ACCOUNT,
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
        normalize_iban,
    ),
    (EntityType.DEVICE, re.compile(r"(?<!\d)\d{15}(?!\d)"), normalize_imei),
    (
        EntityType.EMAIL_ADDRESS,
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        normalize_email,
    ),
    (EntityType.INVOICE_REF, re.compile(r"\bINV-\d{3,8}\b", re.IGNORECASE), normalize_invoice_ref),
    (
        EntityType.PHONE,
        re.compile(r"(?<![\w+])(?:\+?30[ .-]?)?[26]\d{2}[ .-]?\d{3}[ .-]?\d{4}(?![\w-])"),
        normalize_phone,
    ),
)


def _overlaps(start: int, end: int, taken: list[tuple[int, int]]) -> bool:
    return any(start < taken_end and end > taken_start for taken_start, taken_end in taken)


def _matches(text: str) -> Iterator[IdentifierSpan]:
    taken: list[tuple[int, int]] = []
    for entity_type, pattern, normalize in _RULES:
        for match in pattern.finditer(text):
            start, end = match.span()
            if _overlaps(start, end, taken):
                continue
            try:
                key = normalize(match.group(0))
            except NormalizationError:
                continue
            taken.append((start, end))
            yield IdentifierSpan(entity_type, match.group(0), key, start, end)


def find_identifiers(text: str) -> list[IdentifierSpan]:
    """Every identifier occurrence in `text`, in text order, non-overlapping."""
    return sorted(_matches(text), key=lambda span: span.char_start)


def identifier_key(entity_type: EntityType, raw: str) -> str | None:
    """Normalize a value the way the matching rule would, or None if no rule applies."""
    for rule_type, _, normalize in _RULES:
        if rule_type is entity_type:
            try:
                return normalize(raw)
            except NormalizationError:
                return None
    return None
