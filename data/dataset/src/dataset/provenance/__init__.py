"""Evidence citation and provenance-graph construction for the ground-truth layer."""

from dataset.provenance.catalog import _source_refs, build_source_ref_catalog
from dataset.provenance.dag import build_provenance_dags
from dataset.provenance.locators import (
    _field_locator,
    _field_set_locator,
    _nested_value,
    _text_span_locator,
)
from dataset.provenance.relationship import _relationship_assertion

__all__ = [
    "_field_locator",
    "_field_set_locator",
    "_nested_value",
    "_relationship_assertion",
    "_source_refs",
    "_text_span_locator",
    "build_provenance_dags",
    "build_source_ref_catalog",
]
