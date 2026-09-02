from datetime import UTC, datetime

import pytest
from ingestion.domain.normalization import (
    NormalizationError,
    money_to_minor_units,
    normalize_email,
    normalize_iban,
    normalize_imei,
    normalize_invoice_ref,
    normalize_phone,
    rfc2822_to_utc,
    to_utc,
)


@pytest.mark.parametrize(
    "raw", ["+30 697 123 4567", "697 123 4567", "306971234567", "+306971234567"]
)
def test_four_phone_variants_share_one_key(raw: str) -> None:
    assert normalize_phone(raw) == "306971234567"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+30 210 445 5667", "302104455667"),
        ("0030 694 987 6543", "306949876543"),
        ("6949876543", "306949876543"),
    ],
)
def test_fixed_line_and_international_prefixes_normalize(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


def test_too_short_value_is_not_a_phone() -> None:
    with pytest.raises(NormalizationError):
        normalize_phone("4401")


def test_money_never_passes_through_binary_floating_point() -> None:
    assert money_to_minor_units("9800.00", "EUR") == 980000
    assert money_to_minor_units("24.50", "EUR") == 2450
    assert money_to_minor_units("0.10", "EUR") == 10
    with pytest.raises(NormalizationError, match="more precision"):
        money_to_minor_units("1.005", "EUR")
    with pytest.raises(NormalizationError, match="unknown currency"):
        money_to_minor_units("1.00", "XXX")


def test_local_offset_normalizes_to_utc() -> None:
    assert to_utc("2026-02-20T09:10:00+02:00") == datetime(2026, 2, 20, 7, 10, tzinfo=UTC)
    assert to_utc("2026-03-05T14:30:00Z") == datetime(2026, 3, 5, 14, 30, tzinfo=UTC)
    with pytest.raises(NormalizationError, match="no offset"):
        to_utc("2026-03-05T14:30:00")


def test_email_header_date_normalizes_to_utc() -> None:
    assert rfc2822_to_utc("Wed, 4 Mar 2026 18:40:11 +0200") == datetime(
        2026, 3, 4, 16, 40, 11, tzinfo=UTC
    )


def test_other_identifiers_have_canonical_forms() -> None:
    assert (
        normalize_email(" Alex@Meridian-Consulting.example ") == "alex@meridian-consulting.example"
    )
    assert normalize_iban("gr80 0110 0010 0000 0000 0017 719") == "GR8001100010000000000017719"
    assert normalize_imei("356923-107744-818") == "356923107744818"
    assert normalize_invoice_ref("inv-2231") == "INV-2231"
