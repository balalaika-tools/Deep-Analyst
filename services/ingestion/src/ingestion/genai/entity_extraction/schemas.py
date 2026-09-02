"""Model-facing output schema for entity extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EntityTypeLiteral = Literal["PERSON", "ORGANIZATION", "LOCATION"]


class EntityCandidateOut(BaseModel):
    entity_type: EntityTypeLiteral = Field(
        description=(
            "The type of the referent described by the source context: PERSON only for a named "
            "human being, ORGANIZATION only for a named organization, and LOCATION only for a "
            "named place. Never classify a vessel, ship, boat, vehicle, product, device, account, "
            "document, code, or other named object as one of these types merely because its name "
            "is capitalized or person-like."
        )
    )
    text: str = Field(
        min_length=1,
        description=(
            "An exact, contiguous substring copied from the source text. Use the spelling at "
            "the entity's first occurrence; never normalize, correct, or add punctuation."
        ),
    )
    aliases: list[str] = Field(
        default_factory=list,
        description=(
            "Other exact, contiguous spellings of the same entity that also occur in the source "
            "text, in first-occurrence order. Do not invent aliases or repeat text."
        ),
    )


class EntityExtraction(BaseModel):
    entities: list[EntityCandidateOut] = Field(
        default_factory=list,
        description=(
            "Each distinct allowed named human, organization, or place once; empty when the text "
            "contains none. Exclude named objects such as vessels rather than forcing them into an "
            "allowed type."
        ),
    )
