import csv
import io
import json
import sys
import types
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Meeting, Participant, User
from app.reports import lambda_handler
from app.reports.weekly import build_weekly_report, previous_week, report_key, week_monday, week_of
from tests.conftest import TestingSession, engine


@pytest.fixture
def db() -> Iterator[Session]:
    with TestingSession() as session:
        yield session
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE meeting_participants, meetings, participants, users"))


def add_meeting(db: Session, title: str, starts: str, ends: str, owner=None, participants=()) -> None:
    db.add(
        Meeting(
            title=title,
            starts_at=datetime.fromisoformat(starts),
            ends_at=datetime.fromisoformat(ends),
            owner_id=owner.id if owner else None,
            participants=list(participants),
        )
    )
    db.commit()


def sections(report: bytes) -> list[list[list[str]]]:
    """The CSV split into its blank-line separated blocks."""
    blocks: list[list[list[str]]] = [[]]
    for row in csv.reader(io.StringIO(report.decode())):
        if row:
            blocks[-1].append(row)
        else:
            blocks.append([])
    return blocks


def test_iso_weeks():
    assert week_monday("2026-W40").isoformat() == "2026-09-28"
    assert previous_week("2026-W01") == "2025-W52"
    assert report_key("2026-W40") == "reports/2026-W40.csv"
    # Sunday 23:30 UTC is already Monday in Kyiv.
    assert week_of(datetime(2026, 10, 4, 23, 30, tzinfo=UTC)) == "2026-W41"
    for bad in ("2026-40", "last week", "2025-W53"):
        with pytest.raises(ValueError):
            week_monday(bad)


def test_report_totals_change_longest_and_per_organiser(db):
    anna = User(cognito_sub="a", email="anna@example.com")
    oleh = User(cognito_sub="o", email="oleh@example.com")
    people = [Participant(name=f"P{i}", email=f"p{i}@example.com") for i in range(3)]
    db.add_all([anna, oleh, *people])
    db.commit()
    # Week 2026-W40 in Kyiv: Mon 28 Sep 00:00 to Mon 5 Oct 00:00 (+03:00).
    add_meeting(db, "Planning", "2026-09-28T10:00+03:00", "2026-09-28T12:00+03:00", anna, people)
    add_meeting(db, "Standup", "2026-09-29T09:00+03:00", "2026-09-29T09:30+03:00", anna, people[:1])
    add_meeting(db, "Review", "2026-10-02T15:00+03:00", "2026-10-02T16:00+03:00", oleh)
    add_meeting(db, "Retro", "2026-10-04T23:00+03:00", "2026-10-05T00:00+03:00", oleh)
    add_meeting(db, "Legacy", "2026-10-01T08:00+03:00", "2026-10-01T09:00+03:00")
    # Outside the week: the Sunday before (W39) and the Monday after (W41).
    add_meeting(db, "Last week", "2026-09-27T23:59+03:00", "2026-09-28T01:29+03:00", anna)
    add_meeting(db, "Next week", "2026-10-05T00:00+03:00", "2026-10-05T05:00+03:00", anna)

    header, totals, longest, organisers = sections(build_weekly_report("2026-W40", db))

    assert header == [
        ["Weekly meetings report", "2026-W40"],
        ["Period", "2026-09-28", "2026-10-04", "Europe/Kyiv"],
    ]
    assert totals == [
        ["Metric", "This week (2026-W40)", "Previous week (2026-W39)", "Change"],
        ["Meetings", "5", "1", "+4"],
        ["Hours", "5.50", "1.50", "+4.00"],
    ]
    assert longest == [
        ["Longest meetings"],
        ["Rank", "Title", "Start", "Duration (h)", "Participants"],
        ["1", "Planning", "2026-09-28 10:00", "2.00", "3"],
        ["2", "Legacy", "2026-10-01 08:00", "1.00", "0"],
        ["3", "Review", "2026-10-02 15:00", "1.00", "0"],
        ["4", "Retro", "2026-10-04 23:00", "1.00", "0"],
        ["5", "Standup", "2026-09-29 09:00", "0.50", "1"],
    ]
    assert organisers == [
        ["Per organiser"],
        ["Organiser", "Meetings", "Hours"],
        ["anna@example.com", "2", "2.50"],
        ["oleh@example.com", "2", "2.00"],
        ["(no organiser)", "1", "1.00"],
    ]


def test_same_week_gives_the_same_file(db):
    add_meeting(db, "Planning", "2026-09-28T10:00+03:00", "2026-09-28T11:00+03:00")
    assert build_weekly_report("2026-W40", db) == build_weekly_report("2026-W40", db)


def test_empty_week(db):
    _, totals, longest, organisers = sections(build_weekly_report("2026-W40", db))
    assert totals[1:] == [["Meetings", "0", "0", "+0"], ["Hours", "0.00", "0.00", "+0.00"]]
    assert len(longest) == 2 and len(organisers) == 2


# --- report-builder handler -------------------------------------------------------------------


@pytest.fixture
def s3(monkeypatch):
    """A fake boto3 whose S3 client records put_object calls, and a fixed report body."""
    puts: list[dict] = []
    client = types.SimpleNamespace(put_object=lambda **kwargs: puts.append(kwargs))
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda name: client))
    monkeypatch.setattr(lambda_handler, "build_weekly_report", lambda week: f"csv {week}".encode())
    monkeypatch.setattr(settings, "reports_bucket", "meetings-reports-123")
    return puts


def sqs_event(*bodies: dict) -> dict:
    return {"Records": [{"eventSource": "aws:sqs", "body": json.dumps(body)} for body in bodies]}


def test_sqs_message_with_a_week(s3, caplog):
    caplog.set_level("INFO", logger="report-builder")
    result = lambda_handler.handler(sqs_event({"week": "2026-W39", "source": "make"}), None)

    assert result == {"reports": ["reports/2026-W39.csv"]}
    assert s3 == [
        {
            "Bucket": "meetings-reports-123",
            "Key": "reports/2026-W39.csv",
            "Body": b"csv 2026-W39",
            "ContentType": "text/csv",
        }
    ]
    assert "report-builder triggered trigger=sqs via=sqs week=2026-W39" in caplog.messages


def test_scheduled_message_builds_last_week(s3, caplog, monkeypatch):
    caplog.set_level("INFO", logger="report-builder")
    monkeypatch.setattr(
        lambda_handler,
        "datetime",
        types.SimpleNamespace(now=lambda tz: datetime(2026, 10, 12, 4, 0, tzinfo=UTC)),
    )

    lambda_handler.handler(sqs_event({"week": None, "source": "schedule"}), None)

    assert [put["Key"] for put in s3] == ["reports/2026-W41.csv"]
    assert "report-builder triggered trigger=schedule via=sqs week=2026-W41" in caplog.messages


def test_direct_invocation(s3, caplog):
    caplog.set_level("INFO", logger="report-builder")
    lambda_handler.handler({"week": "2026-W40", "source": "schedule"}, None)
    lambda_handler.handler({"week": "2026-W38"}, None)

    assert [put["Key"] for put in s3] == ["reports/2026-W40.csv", "reports/2026-W38.csv"]
    assert "report-builder triggered trigger=schedule via=invoke week=2026-W40" in caplog.messages
    assert "report-builder triggered trigger=invoke via=invoke week=2026-W38" in caplog.messages


def test_failures_raise_so_sqs_retries(s3, monkeypatch):
    monkeypatch.setattr(settings, "report_fail_week", "2026-W30")
    with pytest.raises(RuntimeError):
        lambda_handler.handler(sqs_event({"week": "2026-W30"}), None)
    with pytest.raises(ValueError):
        lambda_handler.handler(sqs_event({"week": "not-a-week"}), None)
    assert s3 == []
