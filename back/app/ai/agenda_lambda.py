"""agenda-parser Lambda (infra/ai.yml): POST pasted text to the function URL, get a suggested agenda back.

It runs the backend image with this module as its handler, outside the VPC: it needs Bedrock and
Cognito's signing keys, never the database. It only suggests; the browser saves the agenda the user
checked through the main API (PUT /api/meetings/{id}/agenda).

Request:  POST {"text": "...", "today": "2026-10-19", "tz": "Europe/Kyiv"}
          with Authorization: Bearer <Cognito ID token>
Answers:  200 {"agenda": {...}, "model": "...", "prompt_version": "..."}; 400 bad body; 401 no or bad
          token; 405 not POST; 413 text over 10,000 characters; 422 no agenda in the text;
          429 Bedrock is throttling; 503 Bedrock unreachable or failing.
"""

import base64
import json
import logging
import time
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anthropic
from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.ai.agenda import PROMPT_VERSION, AgendaParseError, cost_usd, parse_agenda
from app.auth import Identity, unauthorized, verify_id_token
from app.config import settings

log = logging.getLogger("agenda-parser")
log.setLevel(logging.INFO)

MAX_TEXT_LENGTH = 10_000


class ParseRequest(BaseModel):
    text: str = Field(min_length=1)
    # The user's "Monday" is in the user's time zone, not the function's (UTC), so the browser sends both.
    today: date
    tz: str

    @field_validator("tz")
    @classmethod
    def known_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown time zone: {value}") from exc
        return value


def verify_cognito_token(authorization: str) -> Identity:
    """Every call costs money, so there is no anonymous mode here, unlike the API with auth off."""
    if not settings.auth_enabled:
        raise unauthorized("Sign-in is not configured")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized("Sign in required")
    return verify_id_token(token)


def respond(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def log_parse(event: str, user: str, usage: dict[str, int], started: float, **extra: Any) -> None:
    log.info(
        json.dumps(
            {
                "event": event,
                "user": user,
                "model": settings.parser_model,
                "prompt_version": PROMPT_VERSION,
                **usage,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "cost_usd": cost_usd(settings.parser_model, usage),
                **extra,
            }
        )
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if event.get("requestContext", {}).get("http", {}).get("method", "POST") != "POST":
        return respond(405, {"error": "Use POST"})
    try:
        user = verify_cognito_token((event.get("headers") or {}).get("authorization", ""))
    except HTTPException as exc:
        return respond(401, {"error": exc.detail})

    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", errors="replace")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return respond(400, {"error": "The body must be JSON"})
    if not isinstance(body, dict):
        return respond(400, {"error": "The body must be a JSON object"})
    # Checked before anything else about the text, and before the model sees a single token of it.
    if isinstance(body.get("text"), str) and len(body["text"]) > MAX_TEXT_LENGTH:
        return respond(413, {"error": f"The text is longer than {MAX_TEXT_LENGTH:,} characters"})
    try:
        request = ParseRequest.model_validate(body)
    except ValidationError as exc:
        return respond(400, {"error": "; ".join(e["msg"] for e in exc.errors())})

    started = time.monotonic()
    no_usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        agenda, usage = parse_agenda(request.text, request.today, request.tz)
    except AgendaParseError as exc:
        log_parse("agenda_parse_failed", user.sub, exc.usage, started, error=str(exc))
        return respond(422, {"error": "Could not find an agenda in this text"})
    except anthropic.RateLimitError:
        log_parse("agenda_parse_failed", user.sub, no_usage, started, error="throttled")
        return respond(429, {"error": "The model is busy, try again in a minute"})
    except (anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
        if isinstance(exc, anthropic.APIStatusError) and exc.status_code < 500:
            raise  # 400/403/404: a bug or a configuration error, not something the user can wait out
        log_parse("agenda_parse_failed", user.sub, no_usage, started, error=type(exc).__name__)
        return respond(503, {"error": "The model is not available right now"})

    log_parse("agenda_parsed", user.sub, usage, started)
    return respond(
        200,
        {
            "agenda": agenda.model_dump(mode="json"),
            "model": settings.parser_model,
            "prompt_version": PROMPT_VERSION,
        },
    )
