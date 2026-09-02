"""Model-facing output schema for relationship extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PredicateLiteral = Literal["USES", "ASSOCIATED_WITH", "DIRECTOR_OF", "KIN_OF"]
EndpointTypeLiteral = Literal[
    "PERSON",
    "ORGANIZATION",
    "LOCATION",
    "PHONE",
    "EMAIL_ADDRESS",
    "DEVICE",
    "FINANCIAL_ACCOUNT",
    "INVOICE_REF",
]


class RelationshipCandidateOut(BaseModel):
    predicate: PredicateLiteral = Field(
        description=(
            "One allowed predicate whose configured subject/object types match. For USES, a "
            "conventional message signature or contact block that directly pairs a person's name "
            "with a phone number is an explicit contact attribution."
        )
    )
    subject_type: EndpointTypeLiteral = Field(
        description="The exact type shown for the subject in KNOWN ENTITIES."
    )
    subject_text: str = Field(
        min_length=1,
        description="An exact subject spelling copied from KNOWN ENTITIES.",
    )
    object_type: EndpointTypeLiteral = Field(
        description="The exact type shown for the object in KNOWN ENTITIES."
    )
    object_text: str = Field(
        min_length=1,
        description="An exact object spelling copied from KNOWN ENTITIES.",
    )
    quote: str = Field(
        min_length=1,
        description=(
            "The shortest exact, contiguous sentence, clause, label, or contact block copied from "
            "the source text that states the relationship; never paraphrase or normalize it. For "
            "a multi-line signature, include both endpoint lines and preserve the newline exactly."
        ),
    )


class RelationshipExtraction(BaseModel):
    relationships: list[RelationshipCandidateOut] = Field(
        default_factory=list,
        description=(
            "Each distinct explicit relationship once; empty only after checking every allowed "
            "predicate against the known entities."
        ),
    )
