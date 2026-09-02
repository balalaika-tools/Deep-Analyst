import pytest
from evidence_model import (
    FieldLocator,
    MissingProvenance,
    SourceRef,
    TextSpanLocator,
    require_source_refs,
)
from pydantic import ValidationError

SAMPLE = "A. Mavridis uses telephone +30 697 123 4567. There is a possible association."


def test_text_span_quote_slices_from_sample_text() -> None:
    start = SAMPLE.index("uses telephone")
    quote = "uses telephone +30 697 123 4567"
    locator = TextSpanLocator(
        field="text", char_start=start, char_end=start + len(quote), quote=quote
    )

    assert SAMPLE[locator.char_start : locator.char_end] == quote
    assert locator.matches(SAMPLE)
    assert not locator.matches(SAMPLE.replace("697", "698"))


def test_text_span_rejects_offsets_that_disagree_with_the_quote() -> None:
    with pytest.raises(ValidationError, match="quote length"):
        TextSpanLocator(field="text", char_start=0, char_end=3, quote="four")
    with pytest.raises(ValidationError, match="char_end must be greater"):
        TextSpanLocator(field="text", char_start=5, char_end=5, quote="")


def test_source_ref_round_trips_both_locator_kinds() -> None:
    field_ref = SourceRef(record_id="bank:t_88", locator=FieldLocator(field="creditor_iban"))
    span_ref = SourceRef(
        record_id="docs:R-01",
        locator=TextSpanLocator(field="text", char_start=0, char_end=2, quote="A."),
    )

    for ref in (field_ref, span_ref):
        assert SourceRef.model_validate(ref.model_dump(mode="json")) == ref
    assert field_ref.locator.kind == "field"
    assert span_ref.locator.kind == "text_span"


def test_at_least_one_source_reference_is_required() -> None:
    with pytest.raises(MissingProvenance):
        require_source_refs([])
