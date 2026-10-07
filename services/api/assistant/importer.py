"""Strict, side-effect-free parsing of owner-supplied WhatsApp text exports.

Exports do not carry trustworthy account identity or a completeness guarantee.
Callers supply their date order and timezone and explicitly confirm an observed
sender label before using that sender's text as owner-authored style examples.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class ParseRecord:
    ordinal: int
    sender: str | None
    text: str
    timestamp: datetime
    is_system: bool
    is_media: bool
    excluded_from_learning: bool


@dataclass(frozen=True, slots=True)
class ParseResult:
    records: list[ParseRecord]
    senders: list[str]
    warnings: list[str]
    source_hash: str
    oldest: str | None
    newest: str | None


_DIRECTION_MARKS = frozenset(
    "\ufeff\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
_EXPORT_SPACES = frozenset("\u00a0\u202f")
_DATE = r"(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4})"
_TIME = (
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
    r"(?:\s+(?P<ampm>[aApP]\.?[mM]\.?))?"
)
_IOS_HEADER = re.compile(r"^\[" + _DATE + r",?\s+" + _TIME + r"\]\s?(?P<body>.*)$")
_ANDROID_HEADER = re.compile(
    r"^" + _DATE + r",?\s+" + _TIME + r"\s+-\s(?P<body>.*)$"
)
_DATE_LIKE = re.compile(r"^\[?\d{1,4}[./-]\d{1,2}[./-]\d{1,4}(?:,|\s+\d[^\n]*:)")
_MEDIA = re.compile(
    r"^(?:<\s*(?:media|image|video|audio|sticker|gif|document|contact card|voice message)"
    r"\s+omitted\s*>|(?:media|image|video|audio|sticker|gif|document|contact card|voice message)"
    r"\s+omitted\b|<\s*attached\s*:|[^\n]+\(file attached\))",
    re.IGNORECASE,
)
_DELETED = re.compile(
    r"^(?:(?:this message (?:was|has been) deleted)|(?:you deleted this message))\.?$",
    re.IGNORECASE,
)
_CALL_EVENT = re.compile(
    r"^(?:(?:missed|canceled) (?:voice|video) call|(?:voice|video) call(?:,.*)?)\.?$",
    re.IGNORECASE,
)
_FORWARDED = re.compile(r"^(?:\[forwarded(?: many times)?\]|forwarded(?: many times)?\n)", re.IGNORECASE)
# Some system events contain a colon (for example a changed group description).
# Match only their event preamble, which must not already include a sender delimiter.
_SYSTEM = re.compile(
    r"^(?:messages and calls are end-to-end encrypted\b|messages to this chat and calls are now secured\b|"
    r"you(?:'re| are) now an admin\b|you (?:created (?:this )?group|left|were added|were removed)\b|"
    r"[^:]*\b(?:changed (?:the|this)(?: group(?:'s)?)? (?:subject|description|icon|name|settings)|"
    r"changed their phone number|changed (?:his|her|their|your) security code|"
    r"joined using this group's invite link|turned (?:on|off) disappearing messages|"
    r"enabled disappearing messages|disabled disappearing messages)\b)",
    re.IGNORECASE,
)
_MAX_EXPORT_CHARACTERS = 16 * 1024 * 1024


def _header_view(line: str) -> tuple[str, list[int]]:
    """Normalize formatting characters while retaining offsets into the raw line."""
    view: list[str] = []
    positions: list[int] = []
    for position, character in enumerate(line):
        if character in _DIRECTION_MARKS:
            continue
        view.append(" " if character in _EXPORT_SPACES else character)
        positions.append(position)
    positions.append(len(line))
    return "".join(view), positions


def _label(label: str) -> str:
    return _header_view(label)[0].strip()


def _timestamp(match: re.Match[str], order: str, zone: ZoneInfo, line_number: int) -> datetime:
    parts = re.split(r"([./-])", match["date"])
    if parts[1] != parts[3]:
        raise ValueError(f"Inconsistent date separators at line {line_number}")
    tokens = (parts[0], parts[2], parts[4])
    date_tokens = dict(zip(order, tokens, strict=True))
    if len(date_tokens["Y"]) not in (2, 4) or any(
        len(date_tokens[component]) > 2 for component in ("D", "M")
    ):
        raise ValueError(f"Date does not match {order} at line {line_number}")
    year = int(date_tokens["Y"])
    if len(date_tokens["Y"]) == 2:
        # Use Python's documented strptime convention for two-digit export years.
        year += 2000 if year <= 68 else 1900
    hour, minute, second = int(match["hour"]), int(match["minute"]), int(match["second"] or "0")
    ampm = match["ampm"]
    if ampm:
        if not 1 <= hour <= 12:
            raise ValueError(f"Invalid 12-hour timestamp at line {line_number}")
        hour = hour % 12 + (12 if ampm.lower().replace(".", "") == "pm" else 0)
    try:
        local = datetime(year, int(date_tokens["M"]), int(date_tokens["D"]), hour, minute, second)
    except ValueError as exc:
        raise ValueError(f"Invalid date or time at line {line_number}") from exc

    # A timezone alone cannot distinguish repeated clock times. Reject both
    # ambiguous folds and nonexistent spring-forward times instead of guessing.
    possible: set[datetime] = set()
    for fold in (0, 1):
        try:
            candidate = local.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            if candidate.astimezone(zone).replace(tzinfo=None) == local:
                possible.add(candidate)
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Timestamp is outside the supported UTC range at line {line_number}") from exc
    if not possible:
        raise ValueError(f"Nonexistent local timestamp at line {line_number}; check the timezone")
    if len(possible) != 1:
        raise ValueError(f"Ambiguous local timestamp at line {line_number}; an offset is required")
    return possible.pop()


def _sender_and_text(body: str) -> tuple[str | None, str]:
    view, positions = _header_view(body)
    if _SYSTEM.match(view):
        return None, body
    delimiter = re.search(r":\s", view)
    if delimiter is None:
        return None, body
    sender = view[: delimiter.start()].strip()
    if not sender:
        raise ValueError("A timestamped message has an empty sender label")
    if len(sender) > 160:
        raise ValueError("A sender label exceeds 160 characters")
    # Only the export separator belongs to the header; preserve body whitespace.
    return sender, body[positions[delimiter.end()] :]


def parse_export(
    text: str,
    *,
    date_order: str,
    timezone: str,
    owner_sender_label: str | None = None,
    max_records: int = 20_000,
) -> ParseResult:
    """Parse Android/iOS export text without deduplicating or inferring ownership.

    Dates are numeric and their order must be explicitly supplied as DMY, MDY,
    or YMD. Unsupported/malformed timestamp boundaries and content before the
    first record fail the import. Record ordinals are one-based source identities.
    Raw-source SHA-256 is suitable for exact-file deduplication only.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Export text is empty")
    if len(text) > _MAX_EXPORT_CHARACTERS:
        raise ValueError("Export exceeds the 16 MiB character limit")
    if not isinstance(date_order, str) or date_order.strip().upper() not in {"DMY", "MDY", "YMD"}:
        raise ValueError("date_order must explicitly be DMY, MDY, or YMD")
    order = date_order.strip().upper()
    if not isinstance(max_records, int) or isinstance(max_records, bool) or max_records <= 0:
        raise ValueError("max_records must be a positive integer")
    if not isinstance(timezone, str) or not timezone.strip():
        raise ValueError("A valid IANA timezone is required")
    try:
        zone = ZoneInfo(timezone.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("A valid IANA timezone is required") from exc
    if owner_sender_label is not None:
        if not isinstance(owner_sender_label, str) or not _label(owner_sender_label):
            raise ValueError("owner_sender_label must be a nonempty observed sender label")
        owner_sender_label = _label(owner_sender_label)
    try:
        source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeEncodeError as exc:
        raise ValueError("Export text is not valid Unicode") from exc

    pending: list[tuple[datetime, str | None, list[str]]] = []
    senders: list[str] = []
    seen_senders: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        view, positions = _header_view(line)
        match = _IOS_HEADER.match(view) or _ANDROID_HEADER.match(view)
        if match:
            if len(pending) >= max_records:
                raise ValueError(f"Export exceeds the limit of {max_records} records")
            timestamp = _timestamp(match, order, zone, line_number)
            body = line[positions[match.start("body")] :]
            sender, message = _sender_and_text(body)
            if sender is not None and sender not in seen_senders:
                senders.append(sender)
                seen_senders.add(sender)
            pending.append((timestamp, sender, [message]))
        elif _DATE_LIKE.match(view):
            raise ValueError(f"Malformed or unsupported timestamp at line {line_number}")
        elif pending:
            pending[-1][2].append(line)
        elif view.strip():
            raise ValueError(f"Content before the first supported timestamp at line {line_number}")
    if not pending:
        raise ValueError("No supported timestamped messages were found")
    if owner_sender_label is not None and owner_sender_label not in senders:
        raise ValueError("owner_sender_label does not match an observed sender; confirm the mapping")

    records: list[ParseRecord] = []
    for ordinal, (timestamp, sender, lines) in enumerate(pending, 1):
        message = "\n".join(lines)
        if not _header_view(message)[0].strip():
            raise ValueError(f"Record {ordinal} has no message or system-event content")
        message_view = _header_view(message)[0].strip()
        system = sender is None
        media = bool(_MEDIA.match(message_view))
        excluded = bool(
            system
            or media
            or _DELETED.fullmatch(message_view)
            or _CALL_EVENT.fullmatch(message_view)
            or _FORWARDED.match(message_view)
            or owner_sender_label is None
            or sender != owner_sender_label
        )
        records.append(ParseRecord(ordinal, sender, message, timestamp, system, media, excluded))

    warnings = ["History completeness is unknown; coverage describes only this export."]
    if owner_sender_label is None:
        warnings.append("Owner sender mapping is unconfirmed; all records are excluded from style learning.")
    if any(record.is_media for record in records):
        warnings.append("Media placeholders are present; media contents were not imported or learned.")
    if any(record.is_system for record in records):
        warnings.append("System events are retained as export context and excluded from style learning.")
    if any(records[index].timestamp < records[index - 1].timestamp for index in range(1, len(records))):
        warnings.append("Timestamps are out of chronological order; source record order was preserved.")
    times = [record.timestamp for record in records]
    return ParseResult(records, senders, warnings, source_hash, min(times).isoformat(), max(times).isoformat())
