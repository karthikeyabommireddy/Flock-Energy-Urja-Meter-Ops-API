"""Scalar clean-up rules for portal values: text, numbers, timestamps and "Name (CODE)" labels.

Pure functions, no I/O. (The v1 helpers `normalize_whitespace` and `normalize_number` stay in
`app/client.py`, where the v1 tests import them from.)
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
#: Day first, 24 hour, no zone: "23/06/2026 23:30". Day-first is certain: the captures run 23/06 to 30/06.
PORTAL_TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M"

_WHITESPACE = re.compile(r"\s+")
_LABEL = re.compile(r"^(?P<name>.*?)\s*\((?P<code>[^()]+)\)\s*$")
_PLACEHOLDERS = frozenset({"", "-", "--", "—", "n/a", "na", "null", "none", "nil"})


def clean_text(value: Any) -> str | None:
    """Collapse whitespace. None and placeholder tokens ('', '-', 'N/A', ...) become None."""
    if value is None:
        return None
    text = _WHITESPACE.sub(" ", str(value)).strip()
    return None if text.casefold() in _PLACEHOLDERS else text


def parse_float(value: Any) -> float | None:
    """'42594.05' -> 42594.05. Blanks and placeholders -> None; any other non-number raises ValueError."""
    text = clean_text(value)
    if text is None:
        return None
    number = float(text.replace(",", ""))
    if not math.isfinite(number):
        raise ValueError(f"not a finite number: {value!r}")
    return number


def split_label(value: Any) -> tuple[str | None, str | None]:
    """'Jaipur Zone 2 (Z-02)' -> ('Jaipur Zone 2', 'Z-02'). Without parentheses: (the text, None)."""
    text = clean_text(value)
    if text is None:
        return None, None
    match = _LABEL.match(text)
    if match is None:
        return text, None
    return clean_text(match.group("name")), clean_text(match.group("code"))


def parse_portal_timestamp(value: str, tz: ZoneInfo) -> datetime:
    """'23/06/2026 23:30' -> aware UTC datetime.

    The portal states no time zone anywhere, so `tz` is an assumption (see PROTOCOL.md): the naive
    reading is taken to be local time in `tz` and converted.

    Limit: a zone with daylight saving time repeats one wall-clock hour each autumn, and both occurrences
    read the same here (the first one), so the second would be dropped as a duplicate reading. The default,
    Asia/Kolkata, has no daylight saving; with a zone that does, expect one lost hour a year.
    """
    return datetime.strptime(value.strip(), PORTAL_TIMESTAMP_FORMAT).replace(tzinfo=tz).astimezone(UTC)


def to_rfc3339(moment: datetime) -> str:
    """Aware datetime -> '2026-06-23T18:00:00Z' (UTC, second precision)."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
