"""Chunks with character offsets. Fixture records stay whole; the split path is a size guard."""

from __future__ import annotations

from dataclasses import dataclass

_SEPARATORS = ("\n\n", "\n", ". ", " ")


@dataclass(frozen=True, slots=True)
class Chunk:
    char_start: int
    char_end: int
    text: str

    def slices(self, source: str) -> bool:
        return source[self.char_start : self.char_end] == self.text


def _boundary_at_or_before(text: str, limit: int, floor: int) -> int:
    """The largest separator boundary in (floor, limit]; `limit` when none exists."""
    for separator in _SEPARATORS:
        position = text.rfind(separator, floor, limit)
        if position > floor:
            return position + len(separator)
    return limit


def chunk_text(text: str, *, window_chars: int, overlap_chars: int) -> list[Chunk]:
    """One whole chunk when the text fits; otherwise separator-aware windows with overlap.

    Every chunk satisfies `text[char_start:char_end] == chunk.text`, which is what makes
    a citation verifiable against the parent record.
    """
    if overlap_chars >= window_chars:
        raise ValueError("overlap must be smaller than the window")
    if not text:
        return []
    if len(text) <= window_chars:
        return [Chunk(0, len(text), text)]

    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        limit = min(start + window_chars, len(text))
        end = len(text) if limit == len(text) else _boundary_at_or_before(text, limit, start)
        chunks.append(Chunk(start, end, text[start:end]))
        if end == len(text):
            break
        next_start = max(end - overlap_chars, start + 1)
        # Do not start a chunk in the middle of a word when a boundary is close.
        space = text.rfind(" ", start + 1, next_start + 1)
        start = space + 1 if space > start else next_start
    return chunks
