import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient


def create_participant(client: TestClient, name: str, email: str) -> dict:
    response = client.post("/api/participants", json={"name": name, "email": email})
    assert response.status_code == 201, response.text
    return response.json()


def meeting_payload(**overrides) -> dict:
    payload = {
        "title": "Sprint planning",
        "description": "Plan sprint 12 scope",
        "call_link": "https://meet.google.com/abc-defg-hij",
        "place": "Room 204",
        "starts_at": "2026-09-28T10:00:00+03:00",
        "ends_at": "2026-09-28T11:00:00+03:00",
        "participant_ids": [],
    }
    payload.update(overrides)
    return payload


def test_health(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_list_and_delete_meeting(client: TestClient):
    anna = create_participant(client, "Anna", "anna@example.com")
    oleh = create_participant(client, "Oleh", "oleh@example.com")

    response = client.post("/api/meetings", json=meeting_payload(participant_ids=[anna["id"], oleh["id"]]))
    assert response.status_code == 201, response.text
    meeting = response.json()
    uuid.UUID(meeting["id"])
    assert datetime.fromisoformat(meeting["starts_at"]) == datetime.fromisoformat("2026-09-28T07:00:00Z")
    assert datetime.fromisoformat(meeting["ends_at"]) == datetime.fromisoformat("2026-09-28T08:00:00Z")
    assert [p["name"] for p in meeting["participants"]] == ["Anna", "Oleh"]

    listed = client.get("/api/meetings").json()
    assert [m["id"] for m in listed] == [meeting["id"]]

    assert client.delete(f"/api/meetings/{meeting['id']}").status_code == 204
    assert client.get("/api/meetings").json() == []
    assert client.get(f"/api/meetings/{meeting['id']}").status_code == 404


def test_update_meeting(client: TestClient):
    anna = create_participant(client, "Anna", "anna@example.com")
    oleh = create_participant(client, "Oleh", "oleh@example.com")
    meeting = client.post("/api/meetings", json=meeting_payload(participant_ids=[anna["id"]])).json()

    response = client.put(
        f"/api/meetings/{meeting['id']}",
        json=meeting_payload(
            title="Sprint review",
            call_link=None,
            starts_at="2026-09-29T14:00:00Z",
            ends_at="2026-09-29T15:30:00Z",
            participant_ids=[oleh["id"]],
        ),
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["id"] == meeting["id"]
    assert updated["title"] == "Sprint review"
    assert updated["call_link"] is None
    assert datetime.fromisoformat(updated["ends_at"]) == datetime.fromisoformat("2026-09-29T15:30:00Z")
    assert [p["name"] for p in updated["participants"]] == ["Oleh"]
    assert client.get(f"/api/meetings/{meeting['id']}").json() == updated


def test_update_validates_and_checks_existence(client: TestClient):
    meeting = client.post("/api/meetings", json=meeting_payload()).json()
    assert client.put(f"/api/meetings/{meeting['id']}", json=meeting_payload(title="")).status_code == 422
    assert client.put(f"/api/meetings/{uuid.uuid4()}", json=meeting_payload()).status_code == 404
    missing = str(uuid.uuid4())
    response = client.put(f"/api/meetings/{meeting['id']}", json=meeting_payload(participant_ids=[missing]))
    assert response.status_code == 404


def test_meetings_are_listed_by_start_time(client: TestClient):
    for title, start, end in (
        ("Retro", "2026-09-30T15:00:00Z", "2026-09-30T16:00:00Z"),
        ("Standup", "2026-09-28T09:00:00Z", "2026-09-28T09:15:00Z"),
    ):
        response = client.post(
            "/api/meetings", json=meeting_payload(title=title, starts_at=start, ends_at=end)
        )
        assert response.status_code == 201, response.text

    assert [m["title"] for m in client.get("/api/meetings").json()] == ["Standup", "Retro"]


def test_participant_can_join_many_meetings(client: TestClient):
    anna = create_participant(client, "Anna", "anna@example.com")
    for title in ("Standup", "Retro"):
        response = client.post(
            "/api/meetings", json=meeting_payload(title=title, participant_ids=[anna["id"]])
        )
        assert response.status_code == 201

    meetings = client.get("/api/meetings").json()
    assert len(meetings) == 2
    assert all(m["participants"][0]["id"] == anna["id"] for m in meetings)


def test_deleting_meeting_keeps_participants(client: TestClient):
    anna = create_participant(client, "Anna", "anna@example.com")
    meeting = client.post("/api/meetings", json=meeting_payload(participant_ids=[anna["id"]])).json()

    client.delete(f"/api/meetings/{meeting['id']}")

    assert [p["id"] for p in client.get("/api/participants").json()] == [anna["id"]]


def test_duplicate_participant_ids_are_deduplicated(client: TestClient):
    anna = create_participant(client, "Anna", "anna@example.com")
    response = client.post("/api/meetings", json=meeting_payload(participant_ids=[anna["id"], anna["id"]]))
    assert response.status_code == 201
    assert len(response.json()["participants"]) == 1


def test_empty_title_is_rejected(client: TestClient):
    response = client.post("/api/meetings", json=meeting_payload(title="   "))
    assert response.status_code == 422


def test_bad_call_link_is_rejected(client: TestClient):
    response = client.post("/api/meetings", json=meeting_payload(call_link="not a url"))
    assert response.status_code == 422


def test_call_link_or_place_is_required(client: TestClient):
    response = client.post("/api/meetings", json=meeting_payload(call_link=None, place=""))
    assert response.status_code == 422


def test_meeting_must_end_after_it_starts(client: TestClient):
    response = client.post(
        "/api/meetings",
        json=meeting_payload(starts_at="2026-09-28T10:00:00Z", ends_at="2026-09-28T10:00:00Z"),
    )
    assert response.status_code == 422


def test_meeting_time_needs_a_timezone(client: TestClient):
    response = client.post("/api/meetings", json=meeting_payload(starts_at="2026-09-28T10:00:00"))
    assert response.status_code == 422


def test_unknown_participant_returns_404(client: TestClient):
    missing = str(uuid.uuid4())
    response = client.post("/api/meetings", json=meeting_payload(participant_ids=[missing]))
    assert response.status_code == 404
    assert missing in response.json()["detail"]


def test_malformed_uuid_returns_422(client: TestClient):
    assert client.get("/api/meetings/not-a-uuid").status_code == 422
    assert client.delete("/api/meetings/123").status_code == 422


def test_delete_unknown_meeting_returns_404(client: TestClient):
    assert client.delete(f"/api/meetings/{uuid.uuid4()}").status_code == 404


def test_duplicate_participant_email_returns_409(client: TestClient):
    create_participant(client, "Anna", "anna@example.com")
    response = client.post("/api/participants", json={"name": "Anna 2", "email": "ANNA@example.com"})
    assert response.status_code == 409


def test_participant_search(client: TestClient):
    create_participant(client, "Anna", "anna@example.com")
    create_participant(client, "Oleh", "oleh@example.com")
    names = [p["name"] for p in client.get("/api/participants", params={"q": "ole"}).json()]
    assert names == ["Oleh"]


AGENDA = {
    "items": [
        {"topic": "New Pro plans", "owner": "Olena", "minutes": 15},
        {"topic": "Customer feedback", "owner": None, "minutes": None},
    ],
    "action_items": [{"task": "Compare competitors", "owner": "Taras", "due": "2026-10-23"}],
}
SUGGESTION = {
    "agenda": {**AGENDA, "title": "Pricing sync", "language": "en", "duration_minutes": 45},
    "model": "anthropic.claude-opus-5-5",
    "prompt_version": "agenda-v1",
}


def stored_suggestion(meeting_id: str):
    from sqlalchemy import select

    from app.models import Meeting
    from tests.conftest import TestingSession

    with TestingSession() as session:
        return session.scalar(select(Meeting.agenda_ai_suggestion).where(Meeting.id == uuid.UUID(meeting_id)))


def test_new_meeting_has_no_agenda(client: TestClient):
    assert client.post("/api/meetings", json=meeting_payload()).json()["agenda"] is None


def test_save_agenda_with_the_ai_suggestion(client: TestClient):
    meeting = client.post("/api/meetings", json=meeting_payload()).json()
    response = client.put(
        f"/api/meetings/{meeting['id']}/agenda", json={**AGENDA, "ai_suggestion": SUGGESTION}
    )
    assert response.status_code == 200, response.text
    assert response.json()["agenda"] == AGENDA
    assert client.get(f"/api/meetings/{meeting['id']}").json()["agenda"] == AGENDA
    assert "ai_suggestion" not in response.json()

    saved = stored_suggestion(meeting["id"])
    assert saved["model"] == "anthropic.claude-opus-5-5" and saved["prompt_version"] == "agenda-v1"
    assert saved["agenda"]["title"] == "Pricing sync"
    assert saved["agenda"]["action_items"] == AGENDA["action_items"]


def test_agenda_typed_by_hand_keeps_the_earlier_suggestion(client: TestClient):
    meeting = client.post("/api/meetings", json=meeting_payload()).json()
    url = f"/api/meetings/{meeting['id']}/agenda"
    client.put(url, json={**AGENDA, "ai_suggestion": SUGGESTION})
    edited = {"items": [{"topic": "Only item"}], "action_items": []}
    assert client.put(url, json=edited).json()["agenda"]["items"] == [
        {"topic": "Only item", "owner": None, "minutes": None}
    ]
    assert stored_suggestion(meeting["id"])["agenda"]["title"] == "Pricing sync"


def test_editing_the_meeting_keeps_its_agenda(client: TestClient):
    meeting = client.post("/api/meetings", json=meeting_payload()).json()
    client.put(f"/api/meetings/{meeting['id']}/agenda", json=AGENDA)
    updated = client.put(f"/api/meetings/{meeting['id']}", json=meeting_payload(title="Renamed")).json()
    assert updated["agenda"] == AGENDA


def test_empty_agenda_clears_it(client: TestClient):
    meeting = client.post("/api/meetings", json=meeting_payload()).json()
    url = f"/api/meetings/{meeting['id']}/agenda"
    client.put(url, json=AGENDA)
    assert client.put(url, json={"items": [], "action_items": []}).json()["agenda"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"items": [{"topic": ""}]},
        {"items": [{"topic": "Long", "minutes": 0}]},
        {"items": [{"topic": "Long", "minutes": 481}]},
        {"action_items": [{"task": "Do it", "due": "by Friday"}]},
        {"items": [{"topic": "x"}] * 51},
        {**AGENDA, "ai_suggestion": {**SUGGESTION, "model": ""}},
        {**AGENDA, "ai_suggestion": {**SUGGESTION, "agenda": {"title": "no items"}}},
    ],
)
def test_save_agenda_validates(client: TestClient, body: dict):
    meeting = client.post("/api/meetings", json=meeting_payload()).json()
    assert client.put(f"/api/meetings/{meeting['id']}/agenda", json=body).status_code == 422


def test_save_agenda_for_unknown_meeting_returns_404(client: TestClient):
    assert client.put(f"/api/meetings/{uuid.uuid4()}/agenda", json=AGENDA).status_code == 404
