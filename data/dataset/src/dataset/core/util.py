"""Low-level, dependency-free helpers: hashing, file IO, and identifier synthesis."""

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dataset.core.constants import CANONICAL_SEED, CDR_COLUMNS


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            separators=(",", ": "),
        )
    else:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return (text + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _record_hash(record: dict[str, Any]) -> str:
    """Hash a source record using sorted-key canonical JSON plus LF."""
    return _sha256(_json_bytes(record))


def _global_record_id(source_system: str, source_record_id: str) -> str:
    """Qualify a stable source-local identifier for global use."""
    _require(bool(source_system and source_record_id), "record identity parts must be non-empty")
    return f"{source_system}:{source_record_id}"


def _cdr_lexemes(record: dict[str, Any]) -> dict[str, str]:
    """Return the exact string values produced and parsed by the CDR CSV."""
    return {column: "" if record[column] is None else str(record[column]) for column in CDR_COLUMNS}


def _ordered_row_hash(record: dict[str, Any], columns: Sequence[str]) -> str:
    """Hash a SQL row by declared schema order, independent of SQL layout."""
    values = [record.get(column) for column in columns]
    return _sha256(_json_bytes(values))


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _write_text(path: Path, text: str) -> None:
    if not text.endswith("\n"):
        text += "\n"
    _write_bytes(path, text.replace("\r\n", "\n").encode("utf-8"))


def _write_json(path: Path, value: Any) -> None:
    _write_bytes(path, _json_bytes(value, pretty=True))


def _variant_id(seed: int) -> str | None:
    return None if seed == CANONICAL_SEED else str(seed)


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def _imei_check_digit(prefix14: str) -> str:
    if not re.fullmatch(r"\d{14}", prefix14):
        raise ValueError("IMEI prefix must contain 14 digits")
    total = 0
    for index, char in enumerate(prefix14):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            digit = digit // 10 + digit % 10
        total += digit
    return str((10 - total % 10) % 10)


def _make_imei(serial: int) -> str:
    prefix = f"350000{serial:08d}"
    return prefix + _imei_check_digit(prefix)


def _valid_imei(value: str) -> bool:
    return bool(re.fullmatch(r"\d{15}", value)) and value[-1] == _imei_check_digit(value[:14])


def _make_gr_iban(serial: int, ending: str) -> str:
    """Create a MOD-97-valid, synthetic Greek-shaped IBAN."""
    if not re.fullmatch(r"\d{4}", ending):
        raise ValueError("IBAN ending must contain four digits")
    account_component = f"{serial:012d}{ending}"
    bban = "0110001" + account_component
    remainder = int(bban + "162700") % 97  # G=16, R=27, provisional 00
    check_digits = 98 - remainder
    return f"GR{check_digits:02d}{bban}"


def _valid_iban(value: str) -> bool:
    compact = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"GR\d{25}", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(ord(char) - 55) if char.isalpha() else char for char in rearranged)
    return int(numeric) % 97 == 1
