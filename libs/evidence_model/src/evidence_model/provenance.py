"""Source references: the record and the smallest useful locator inside it."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TextSpanLocator(BaseModel):
    """A character span inside one text field of a record, with the quoted text."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["text_span"] = "text_span"
    field: str
    char_start: int = Field(ge=0)
    char_end: int
    quote: str

    @model_validator(mode="after")
    def _offsets_match_quote(self) -> TextSpanLocator:
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if len(self.quote) != self.char_end - self.char_start:
            raise ValueError("quote length must equal char_end - char_start")
        return self

    def matches(self, text: str) -> bool:
        """True when the record text sliced by the offsets equals the quote."""
        return text[self.char_start : self.char_end] == self.quote


class FieldLocator(BaseModel):
    """A structured field of a record."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["field"] = "field"
    field: str


type Locator = Annotated[TextSpanLocator | FieldLocator, Field(discriminator="kind")]


class SourceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_id: str
    locator: Locator


class MissingProvenance(ValueError):
    """An extracted fact was offered without any source reference."""


def require_source_refs(refs: Sequence[SourceRef]) -> list[SourceRef]:
    if not refs:
        raise MissingProvenance("at least one source reference is required")
    return list(refs)
