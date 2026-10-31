"""The weekly meetings report: one ISO week of meetings as a CSV file, with no AWS in it.

The caller always names the week, and one week always maps to one S3 key, so building a week twice
overwrites the same file. That makes the task safe to run more than once ("at least once" delivery).

Run it locally against the compose database:
    DATABASE_URL=postgresql+psycopg://meetings:meetings@localhost:5432/meetings \\
        uv run python -m app.reports.weekly 2026-W40
"""

import csv
import io
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Meeting, User, meeting_participants

_WEEK = re.compile(r"^(\d{4})-W(\d{2})$")
TOP_MEETINGS = 5
NO_ORGANISER = "(no organiser)"


@dataclass(frozen=True)
class Row:
    title: str
    starts_at: datetime
    ends_at: datetime
    organiser: str
    participants: int

    @property
    def hours(self) -> float:
        return (self.ends_at - self.starts_at).total_seconds() / 3600


def week_monday(week: str) -> date:
    """Monday of an ISO week such as "2026-W40"; ValueError for anything else."""
    match = _WEEK.match(week)
    if not match:
        raise ValueError(f"Week must look like 2026-W40, got {week!r}")
    return date.fromisocalendar(int(match[1]), int(match[2]), 1)


def iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def previous_week(week: str) -> str:
    return iso_week(week_monday(week) - timedelta(days=7))


def week_of(moment: datetime) -> str:
    """The ISO week `moment` falls in, in the report's time zone."""
    return iso_week(moment.astimezone(ZoneInfo(settings.report_timezone)).date())


def report_key(week: str) -> str:
    week_monday(week)
    return f"reports/{week}.csv"


def _bounds(week: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(settings.report_timezone)
    monday = week_monday(week)
    return (
        datetime.combine(monday, time(), tzinfo=zone),
        datetime.combine(monday + timedelta(days=7), time(), tzinfo=zone),
    )


def _meetings(db: Session, week: str) -> list[Row]:
    start, end = _bounds(week)
    counts = (
        select(meeting_participants.c.meeting_id, func.count().label("n"))
        .group_by(meeting_participants.c.meeting_id)
        .subquery()
    )
    query = (
        select(Meeting.title, Meeting.starts_at, Meeting.ends_at, User.email, func.coalesce(counts.c.n, 0))
        .outerjoin(User, Meeting.owner_id == User.id)
        .outerjoin(counts, counts.c.meeting_id == Meeting.id)
        .where(Meeting.starts_at >= start, Meeting.starts_at < end)
        .order_by(Meeting.starts_at, Meeting.id)
    )
    return [
        Row(title, starts, ends, email or NO_ORGANISER, n)
        for title, starts, ends, email, n in db.execute(query)
    ]


def _hours(rows: list[Row]) -> float:
    return sum(row.hours for row in rows)


def _csv(week: str, this: list[Row], last: list[Row]) -> bytes:
    zone = ZoneInfo(settings.report_timezone)
    start, end = _bounds(week)
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")

    w.writerow(["Weekly meetings report", week])
    w.writerow(["Period", start.date().isoformat(), (end.date() - timedelta(days=1)).isoformat(), zone.key])
    w.writerow([])
    w.writerow(["Metric", f"This week ({week})", f"Previous week ({previous_week(week)})", "Change"])
    w.writerow(["Meetings", len(this), len(last), f"{len(this) - len(last):+d}"])
    w.writerow(["Hours", f"{_hours(this):.2f}", f"{_hours(last):.2f}", f"{_hours(this) - _hours(last):+.2f}"])
    w.writerow([])
    w.writerow(["Longest meetings"])
    w.writerow(["Rank", "Title", "Start", "Duration (h)", "Participants"])
    # sorted() is stable: equally long meetings keep their start order.
    longest = sorted(this, key=lambda row: row.hours, reverse=True)[:TOP_MEETINGS]
    for rank, row in enumerate(longest, start=1):
        start_local = row.starts_at.astimezone(zone).strftime("%Y-%m-%d %H:%M")
        w.writerow([rank, row.title, start_local, f"{row.hours:.2f}", row.participants])
    w.writerow([])
    w.writerow(["Per organiser"])
    w.writerow(["Organiser", "Meetings", "Hours"])
    organisers: dict[str, list[Row]] = {}
    for row in this:
        organisers.setdefault(row.organiser, []).append(row)
    for organiser, rows in sorted(organisers.items(), key=lambda item: (-_hours(item[1]), item[0])):
        w.writerow([organiser, len(rows), f"{_hours(rows):.2f}"])
    return out.getvalue().encode()


def build_weekly_report(week: str, db: Session | None = None) -> bytes:
    """Query the meetings of one ISO week (e.g. "2026-W40") and return the CSV."""
    week_monday(week)
    if db is None:
        with SessionLocal() as session:
            return build_weekly_report(week, session)
    return _csv(week, _meetings(db, week), _meetings(db, previous_week(week)))


if __name__ == "__main__":
    sys.stdout.write(build_weekly_report(sys.argv[1]).decode())
