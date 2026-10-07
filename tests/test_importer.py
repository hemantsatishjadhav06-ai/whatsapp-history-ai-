import hashlib
from datetime import UTC, datetime

import pytest

from assistant.importer import parse_export


def parse(text, **kwargs):
    return parse_export(text, date_order=kwargs.pop("date_order", "DMY"), timezone=kwargs.pop("timezone", "UTC"), **kwargs)


def test_android_group_export_multiline_system_media_and_owner_mapping():
    source = (
        "06/10/2026, 09:29 - Messages and calls are end-to-end encrypted.\n"
        "06/10/2026, 09:30 - Owner: hello\nthis is another line\n"
        "06/10/2026, 09:31 - Alice: Hi there\n"
        "06/10/2026, 09:32 - +91 98765 43210: <Media omitted>\n"
        "06/10/2026, 09:33 - Owner: <attached: 000003-PHOTO-2026-10-06.jpg>\n"
        "06/10/2026, 09:34 - Owner: This message was deleted"
    )
    result = parse(source, owner_sender_label="Owner", timezone="Asia/Kolkata")
    assert result.senders == ["Owner", "Alice", "+91 98765 43210"]
    assert result.records[1].text == "hello\nthis is another line"
    assert result.records[1].timestamp == datetime(2026, 10, 6, 4, tzinfo=UTC)
    assert result.records[0].sender is None and result.records[0].is_system
    assert [r.excluded_from_learning for r in result.records] == [True, False, True, True, True, True]
    assert result.records[3].is_media and result.records[4].is_media
    assert result.source_hash == hashlib.sha256(source.encode()).hexdigest()
    assert result.oldest == "2026-10-06T03:59:00+00:00"
    assert result.newest == "2026-10-06T04:04:00+00:00"
    assert any("completeness is unknown" in warning for warning in result.warnings)


def test_ios_unicode_markers_and_ampm_keep_message_text():
    source = (
        "\ufeff\u200e[06/10/2026, 12:00:01\u202fAM] \u200eOwner: start\u200e\n"
        "\u200f[06/10/2026, 12:00:02 PM] Alice: afternoon\n"
        "[06/10/2026, 9:30:00 p.m.] Owner: done"
    )
    result = parse(source, owner_sender_label="\u200eOwner")
    assert result.records[0].text == "start\u200e"
    assert [r.timestamp.hour for r in result.records] == [0, 12, 21]
    assert result.senders == ["Owner", "Alice"]


@pytest.mark.parametrize(
    ("date", "date_order", "expected"),
    [
        ("06/10/2026", "dmy", datetime(2026, 10, 6, tzinfo=UTC)),
        ("06/10/2026", "MDY", datetime(2026, 6, 10, tzinfo=UTC)),
        ("2026-10-06", "YMD", datetime(2026, 10, 6, tzinfo=UTC)),
        ("6.10.26", "DMY", datetime(2026, 10, 6, tzinfo=UTC)),
        ("10/6/26", "MDY", datetime(2026, 10, 6, tzinfo=UTC)),
        ("26/10/6", "YMD", datetime(2026, 10, 6, tzinfo=UTC)),
    ],
)
def test_explicit_date_order_and_numeric_locale(date, date_order, expected):
    result = parse(f"{date}, 00:00 - Owner: hello", date_order=date_order)
    assert result.records[0].timestamp == expected


def test_identical_messages_preserve_source_record_identity():
    line = "06/10/2026, 09:30 - Owner: same\n"
    result = parse(line + line, owner_sender_label="Owner")
    assert len(result.records) == 2
    assert [r.ordinal for r in result.records] == [1, 2]
    assert result.records[0].text == result.records[1].text
    assert result.records[0].timestamp == result.records[1].timestamp


def test_owner_identity_is_not_inferred_from_you_label_or_first_sender():
    result = parse("06/10/2026, 09:30 - You: hello\n06/10/2026, 09:31 - Alice: hello")
    assert all(r.excluded_from_learning for r in result.records)
    assert any("unconfirmed" in warning for warning in result.warnings)
    with pytest.raises(ValueError, match="observed sender"):
        parse("06/10/2026, 09:30 - You: hello", owner_sender_label="Owner")


def test_system_event_with_colon_is_not_sender():
    result = parse("06/10/2026, 09:30 - Alice changed the group description: meeting schedule")
    assert result.senders == []
    assert result.records[0].sender is None
    assert result.records[0].is_system


@pytest.mark.parametrize("placeholder", ["image omitted", "GIF omitted", "video.mp4 (file attached)"])
def test_media_formats_are_excluded(placeholder):
    result = parse(f"06/10/2026, 09:30 - Owner: {placeholder}", owner_sender_label="Owner")
    assert result.records[0].is_media
    assert result.records[0].excluded_from_learning


@pytest.mark.parametrize("marker", ["[Forwarded]", "Forwarded", "Forwarded many times"])
def test_explicit_forwarded_content_is_not_owner_style(marker):
    result = parse(f"06/10/2026, 09:30 - Owner: {marker}\nA quoted message", owner_sender_label="Owner")
    assert result.records[0].excluded_from_learning


@pytest.mark.parametrize(
    "source",
    [
        "", "no timestamps here", "Preface\n06/10/2026, 09:30 - Owner: hello",
        "31/02/2026, 09:30 - Owner: hello", "06/10/2026, 25:00 - Owner: hello",
        "06/10/2026, 9:60 - Owner: hello", "06/10/2026, 00:01 AM - Owner: hello",
        "06/10/2026, 13:01 PM - Owner: hello", "06/10/2026, 09:30:60 - Owner: hello",
        "[06/10/2026, 9:30:00 AM Owner: missing bracket",
        "06/10/2026, 09:30 — Owner: unsupported separator",
        "06/10/2026, 09:30 - : missing sender", "06/10/2026, 09:30 - Owner: ",
        "06/10/2026, 09:30 - ", "06/10-2026, 09:30 - Owner: mixed separators",
        "06/10/2026, 09:30 - Owner: hello\n06/10/2026, noon - Owner: malformed record",
    ],
)
def test_malformed_or_impossible_exports_fail_safely(source):
    with pytest.raises(ValueError):
        parse(source)


@pytest.mark.parametrize("date_order", ["", "AUTO", "D/M/Y", None, 123])
def test_date_order_must_be_explicit(date_order):
    with pytest.raises(ValueError, match="date_order"):
        parse("06/10/2026, 09:30 - Owner: hello", date_order=date_order)


@pytest.mark.parametrize("timezone", ["", "Not/AZone", "/tmp/malicious", None, 123])
def test_timezone_must_be_valid(timezone):
    with pytest.raises(ValueError, match="timezone"):
        parse("06/10/2026, 09:30 - Owner: hello", timezone=timezone)


@pytest.mark.parametrize(
    ("timestamp", "error"),
    [("01/11/2026, 01:30", "Ambiguous"), ("08/03/2026, 02:30", "Nonexistent")],
)
def test_dst_ambiguous_or_nonexistent_time_is_not_guessed(timestamp, error):
    with pytest.raises(ValueError, match=error):
        parse(f"{timestamp} - Owner: hello", timezone="America/New_York")


def test_dst_valid_times_convert_individually():
    result = parse(
        "01/11/2026, 00:30 - Owner: before\n01/11/2026, 02:30 - Owner: after",
        timezone="America/New_York",
    )
    assert result.records[0].timestamp.hour == 4
    assert result.records[1].timestamp.hour == 7


def test_out_of_order_export_reports_coverage_without_reordering():
    result = parse("06/10/2026, 10:00 - Owner: later\n06/10/2026, 09:00 - Alice: earlier")
    assert result.records[0].text == "later"
    assert result.oldest == "2026-10-06T09:00:00+00:00"
    assert result.newest == "2026-10-06T10:00:00+00:00"
    assert any("out of chronological order" in warning for warning in result.warnings)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "2"])
def test_invalid_record_limit(limit):
    with pytest.raises(ValueError, match="max_records"):
        parse("06/10/2026, 09:30 - Owner: hi", max_records=limit)


def test_record_limit_and_whitespace_continuation():
    result = parse("\n06/10/2026, 09:30 - Owner: hi\n  indented\n", max_records=1)
    assert result.records[0].text == "hi\n  indented"
    with pytest.raises(ValueError, match="limit of 1"):
        parse("06/10/2026, 09:30 - Owner: hi\n06/10/2026, 09:31 - Alice: hi", max_records=1)


@pytest.mark.parametrize("label", ["", "  ", 123])
def test_empty_or_invalid_owner_mapping(label):
    with pytest.raises(ValueError, match="owner_sender_label"):
        parse("06/10/2026, 09:30 - Owner: hi", owner_sender_label=label)


def test_unsupported_unicode_is_reported_as_validation_error():
    with pytest.raises(ValueError, match="valid Unicode"):
        parse("06/10/2026, 09:30 - Owner: \ud800")


def test_timezone_conversion_range_error_is_validation_error():
    with pytest.raises(ValueError, match="supported UTC range"):
        parse("01/01/0001, 00:00 - Owner: ancient", timezone="Asia/Kolkata")
