from datetime import UTC, datetime
from pathlib import Path

from ingestion.adapters.fixtures.cdr import load_cdr
from ingestion.adapters.fixtures.email import load_emails
from ingestion.adapters.fixtures.extraction import load_extraction


def test_cdr_yields_55_records_with_utc_times_and_originals(edition_dir: Path) -> None:
    batch = load_cdr(edition_dir)

    assert len(batch.records) == len(batch.communications) == 55
    c18 = next(record for record in batch.records if record.source_record_id == "c18")
    assert c18.record_id == "cdr:c18" and c18.record_type == "cdr"
    assert c18.event_time_utc == datetime(2026, 2, 20, 7, 10, tzinfo=UTC)
    assert c18.original_time == "2026-02-20T09:10:00+02:00"
    assert c18.payload["ts_local"] == "2026-02-20T09:10:00+02:00"
    assert c18.text is None and c18.source_path == "raw/cdr.csv"

    c01 = next(comm for comm in batch.communications if comm.record_id == "cdr:c01")
    assert (c01.channel, c01.direction) == ("sms", "out")
    assert (c01.from_endpoint, c01.to_endpoint) == ("306971234567", "306949876543")
    assert c01.device_id == "356923107744818" and c01.duration_s is None
    c05 = next(comm for comm in batch.communications if comm.record_id == "cdr:c05")
    assert c05.device_id is None and c05.duration_s == 620


def test_extraction_yields_18_records_and_direction_aware_endpoints(edition_dir: Path) -> None:
    batch = load_extraction(edition_dir)

    assert len(batch.records) == len(batch.communications) == 18
    by_id = {record.source_record_id: record for record in batch.records}
    assert by_id["X-204"].text == "leaving tomorrow, same place as last time"
    assert by_id["X-204"].event_time_utc == datetime(2026, 3, 4, 21, 14, tzinfo=UTC)
    assert by_id["X-204"].original_time == "2026-03-04T21:14:00Z"
    assert by_id["X-N10"].text is None
    outbound = next(comm for comm in batch.communications if comm.record_id == "extraction:X-204")
    inbound = next(comm for comm in batch.communications if comm.record_id == "extraction:X-205")
    assert (outbound.from_endpoint, outbound.to_endpoint) == ("306971234567", "306949876543")
    assert (inbound.from_endpoint, inbound.to_endpoint) == ("306949876543", "306971234567")
    assert (inbound.from_field, inbound.to_field) == ("peer", "subscriber_msisdn")
    assert outbound.channel == "sms" and inbound.device_id == "356923107744818"


def test_emails_yield_6_records_with_subject_and_body_text(edition_dir: Path) -> None:
    batch = load_emails(edition_dir)

    assert len(batch.records) == len(batch.communications) == 6
    em1 = next(record for record in batch.records if record.source_record_id == "eM1")
    assert em1.event_time_utc == datetime(2026, 3, 4, 16, 40, 11, tzinfo=UTC)
    assert em1.original_time == "Wed, 4 Mar 2026 18:40:11 +0200"
    assert em1.text is not None and em1.text.startswith("Thursday\n\nThe package on Thursday.")
    assert "697 123 4567" in em1.text
    assert em1.payload["from"] == "alex@meridian-consulting.example"
    assert em1.source_path == "raw/emails/eM1.eml"
    comm = next(comm for comm in batch.communications if comm.record_id == "email:eM1")
    assert (comm.from_endpoint, comm.to_endpoint) == (
        "alex@meridian-consulting.example",
        "k.rossi@aegeantrade.example",
    )
    assert comm.channel == "email"
