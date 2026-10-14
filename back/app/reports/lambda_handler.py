"""report-builder Lambda: builds one week's report and saves it to S3 (infra/reports.yaml).

It runs the backend image with this module as its handler, and takes two event shapes:
- an SQS event from the report-requests queue, whose message bodies look like
  {"week": "2026-W39" | null, "source": "schedule" | "make"}: EventBridge Scheduler sends one every
  Monday, `make report-now` sends one on demand;
- a direct invocation with the same payload as the event itself (`aws lambda invoke`).
A missing week means the previous ISO week. The saved file is an S3 event that starts report-mailer;
this function does not know that the mailer exists.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.reports.weekly import build_weekly_report, previous_week, report_key, week_of

log = logging.getLogger("report-builder")
log.setLevel(logging.INFO)


def payloads(event: dict[str, Any]) -> list[dict[str, Any]]:
    """One payload per SQS record, or the event itself for a direct invocation."""
    if "Records" in event:
        return [json.loads(record["body"]) for record in event["Records"]]
    return [event]


def detect_trigger(event: dict[str, Any], payload: dict[str, Any]) -> str:
    if payload.get("source") == "schedule":
        return "schedule"
    return "sqs" if "Records" in event else "invoke"


def save_report(week: str) -> str:
    import boto3  # part of the Lambda runtime

    if week == settings.report_fail_week:
        raise RuntimeError(f"REPORT_FAIL_WEEK={week}: failing on purpose")
    key = report_key(week)
    boto3.client("s3").put_object(
        Bucket=settings.reports_bucket, Key=key, Body=build_weekly_report(week), ContentType="text/csv"
    )
    return key


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    keys = []
    for payload in payloads(event):
        week = payload.get("week") or previous_week(week_of(datetime.now(UTC)))
        trigger = detect_trigger(event, payload)
        via = "sqs" if "Records" in event else "invoke"
        log.info(
            "report-builder triggered trigger=%s via=%s week=%s",
            trigger,
            via,
            week,
            extra={"trigger": trigger, "via": via, "week": week},
        )
        key = save_report(week)
        log.info("report saved s3://%s/%s", settings.reports_bucket, key)
        keys.append(key)
    return {"reports": keys}
