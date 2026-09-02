"""RFC 822 email files to records and communication projections."""

from __future__ import annotations

from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from ingestion.domain.normalization import normalize_email, rfc2822_to_utc
from ingestion.domain.records import (
    CommunicationProjection,
    SourceBatch,
    SourceRecord,
    content_hash,
)

SOURCE_SYSTEM = "email"
RELATIVE_DIR = "raw/emails"


class EmailParseError(ValueError):
    """An email file lacks a header the record envelope needs."""


def _body(message: Message) -> str:
    if message.is_multipart() or message.get_content_type() != "text/plain":
        raise EmailParseError("email must be a single text/plain part")
    raw = message.get_payload(decode=True)
    if not isinstance(raw, bytes):
        raise EmailParseError("email body could not be decoded")
    return raw.decode(message.get_content_charset() or "utf-8").rstrip("\n")


def _header(message: Message, name: str) -> str:
    value = message.get(name)
    if value is None:
        raise EmailParseError(f"email is missing the {name} header")
    return str(value)


def _record(path: Path, relative_path: str) -> tuple[SourceRecord, CommunicationProjection]:
    # compat32 keeps header values verbatim; the original Date lexeme is evidence.
    message = BytesParser(policy=policy.compat32).parsebytes(path.read_bytes())
    from_name, from_addr = parseaddr(_header(message, "From"))
    to_name, to_addr = parseaddr(_header(message, "To"))
    date_header = _header(message, "Date")
    subject = _header(message, "Subject")
    event_time = rfc2822_to_utc(date_header)
    body = _body(message)
    headers = {
        "message_id": message.get("Message-ID"),
        "from": from_addr,
        "from_name": from_name,
        "to": to_addr,
        "to_name": to_name,
        "date": date_header,
        "subject": subject,
        "source_version": message.get("X-Source-Version"),
    }
    sender, recipient = normalize_email(from_addr), normalize_email(to_addr)
    payload: dict[str, Any] = {
        **headers,
        "normalized": {"from": sender, "to": recipient, "event_time_utc": event_time.isoformat()},
    }
    record = SourceRecord(
        source_system=SOURCE_SYSTEM,
        source_record_id=_header(message, "X-Source-Record-ID"),
        record_type="email",
        event_time_utc=event_time,
        original_time=date_header,
        text=f"{subject}\n\n{body}",
        payload=payload,
        source_path=relative_path,
        content_hash=content_hash({**headers, "body": body}),
    )
    projection = CommunicationProjection(
        record_id=record.record_id,
        channel="email",
        direction="sent",
        from_endpoint=sender,
        to_endpoint=recipient,
        from_field="from",
        to_field="to",
        event_time_utc=event_time,
        original_time=date_header,
    )
    return record, projection


def load_emails(edition_dir: Path) -> SourceBatch:
    directory = edition_dir / RELATIVE_DIR
    rows = [
        _record(path, f"{RELATIVE_DIR}/{path.name}") for path in sorted(directory.glob("*.eml"))
    ]
    return SourceBatch(
        source_system=SOURCE_SYSTEM,
        records=[record for record, _ in rows],
        communications=[projection for _, projection in rows],
    )
