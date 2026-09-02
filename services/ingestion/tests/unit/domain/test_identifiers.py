from pathlib import Path

from evidence_model import EntityType
from ingestion.domain.identifiers import find_identifiers, identifier_key


def _document(edition_dir: Path, name: str) -> str:
    return (edition_dir / "raw" / "docs" / f"{name}.md").read_text(encoding="utf-8")


def test_phone_in_r01_is_found_at_the_right_offsets(edition_dir: Path) -> None:
    text = _document(edition_dir, "R-01")
    phones = [s for s in find_identifiers(text) if s.entity_type is EntityType.PHONE]

    assert [s.raw for s in phones] == ["+30 697 123 4567", "+30 691 222 3344"]
    assert phones[0].normalized_key == "306971234567"
    assert text[phones[0].char_start : phones[0].char_end] == "+30 697 123 4567"


def test_national_phone_and_email_in_r02(edition_dir: Path) -> None:
    text = _document(edition_dir, "R-02")
    spans = find_identifiers(text)

    phone = next(s for s in spans if s.entity_type is EntityType.PHONE)
    assert phone.raw == "694 987 6543" and phone.normalized_key == "306949876543"
    assert text[phone.char_start : phone.char_end] == phone.raw
    email = next(s for s in spans if s.entity_type is EntityType.EMAIL_ADDRESS)
    assert email.normalized_key == "sofia@meridian-consulting.example"
    assert not any(s.raw == "4401" for s in spans)


def test_invoice_reference_in_r05(edition_dir: Path) -> None:
    text = _document(edition_dir, "R-05")
    (invoice,) = [s for s in find_identifiers(text) if s.entity_type is EntityType.INVOICE_REF]

    assert invoice.raw == "INV-2231" and invoice.normalized_key == "INV-2231"
    assert text[invoice.char_start : invoice.char_end] == "INV-2231"


def test_longer_identifiers_claim_their_characters_first() -> None:
    text = "IBAN GR8001100010000000000017719, IMEI 356923107744818, call 306971234567."
    spans = find_identifiers(text)

    assert [(s.entity_type, s.normalized_key) for s in spans] == [
        (EntityType.FINANCIAL_ACCOUNT, "GR8001100010000000000017719"),
        (EntityType.DEVICE, "356923107744818"),
        (EntityType.PHONE, "306971234567"),
    ]


def test_dates_amounts_and_short_numbers_are_not_identifiers() -> None:
    text = "On 2026-03-05T14:30:00Z €9,800.00 moved; account ends in 4401; ref 20260305."
    assert find_identifiers(text) == []


def test_identifier_key_normalizes_only_rule_types() -> None:
    assert identifier_key(EntityType.PHONE, "+30 697 123 4567") == "306971234567"
    assert identifier_key(EntityType.PERSON, "Alexandros Mavridis") is None
    assert identifier_key(EntityType.PHONE, "not a phone") is None
