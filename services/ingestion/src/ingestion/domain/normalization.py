"""Deterministic normalization of hard fields. Never a model; originals are retained by callers."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

GREECE_COUNTRY_CODE = "30"
_NON_DIGITS = re.compile(r"\D+")
_WHITESPACE = re.compile(r"\s+")
_MINOR_UNIT_DIGITS = {"EUR": 2, "USD": 2, "GBP": 2, "CHF": 2, "JPY": 0}


class NormalizationError(ValueError):
    """A hard field cannot be normalized without guessing."""


def normalize_phone(raw: str, *, default_country_code: str = GREECE_COUNTRY_CODE) -> str:
    """Canonical digits-only international form, for example `306971234567`.

    Greek national numbers have ten digits and start with 2 (fixed) or 6 (mobile);
    a ten-digit value without a country code is assumed to be Greek.
    """
    digits = _NON_DIGITS.sub("", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits[0] in "26":
        return default_country_code + digits
    if len(digits) == 12 and digits.startswith(default_country_code):
        return digits
    if 11 <= len(digits) <= 15:
        return digits
    raise NormalizationError(f"not a phone number: {raw!r}")


def normalize_email(raw: str) -> str:
    value = raw.strip().casefold()
    if "@" not in value:
        raise NormalizationError(f"not an email address: {raw!r}")
    return value


def normalize_iban(raw: str) -> str:
    value = _WHITESPACE.sub("", raw).upper()
    if len(value) < 15 or not value[:2].isalpha():
        raise NormalizationError(f"not an IBAN: {raw!r}")
    return value


def normalize_imei(raw: str) -> str:
    digits = _NON_DIGITS.sub("", raw)
    if len(digits) != 15:
        raise NormalizationError(f"not an IMEI: {raw!r}")
    return digits


def normalize_invoice_ref(raw: str) -> str:
    return _WHITESPACE.sub("", raw).upper()


def money_to_minor_units(amount_text: str, currency: str) -> int:
    """Exact decimal text to integer minor units; binary floating point never touches it."""
    exponent = _MINOR_UNIT_DIGITS.get(currency.upper())
    if exponent is None:
        raise NormalizationError(f"unknown currency scale: {currency!r}")
    try:
        amount = Decimal(amount_text.strip().replace(",", ""))
    except InvalidOperation as exc:
        raise NormalizationError(f"not a decimal amount: {amount_text!r}") from exc
    scaled = amount.scaleb(exponent)
    if scaled != scaled.to_integral_value():
        raise NormalizationError(f"{amount_text!r} has more precision than {currency} allows")
    return int(scaled)


def to_utc(value: str) -> datetime:
    """An ISO 8601 timestamp with an offset, converted to UTC. Naive values are rejected."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NormalizationError(f"not an ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise NormalizationError(f"timestamp has no offset: {value!r}")
    return parsed.astimezone(UTC)


def rfc2822_to_utc(value: str) -> datetime:
    """An email Date header converted to UTC."""
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise NormalizationError(f"not an RFC 2822 date: {value!r}") from exc
    if parsed.tzinfo is None:
        raise NormalizationError(f"email date has no offset: {value!r}")
    return parsed.astimezone(UTC)
