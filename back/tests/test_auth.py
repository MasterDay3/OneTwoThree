import json
import time
from collections.abc import Iterator

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import auth
from app.config import settings
from tests.test_api import meeting_payload

POOL_ID = "us-east-1_TestPool"
CLIENT_ID = "test-client"
ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL_ID}"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def cognito(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
    monkeypatch.setattr(settings, "cognito_region", "us-east-1")
    monkeypatch.setattr(settings, "cognito_user_pool_id", POOL_ID)
    monkeypatch.setattr(settings, "cognito_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "cognito_jwks", json.dumps({"keys": [{**jwk, "kid": "k1"}]}))
    auth.signing_keys.cache_clear()
    yield
    auth.signing_keys.cache_clear()


def token(sub: str = "sub-anna", email: str = "Anna@Example.com", key=KEY, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "email": email,
        "name": "Anna",
        "aud": CLIENT_ID,
        "iss": ISSUER,
        "token_use": "id",
        "iat": now,
        "exp": now + 3600,
        **overrides,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def test_health_is_public(client: TestClient):
    assert client.get("/api/health").status_code == 200


def test_root_redirects_to_the_api_docs_without_a_token(client: TestClient):
    response = client.get("/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/api/docs"


def test_api_requires_a_token(client: TestClient):
    for path in ("/api/meetings", "/api/participants", "/api/me"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "bad",
    [
        token(key=OTHER_KEY),
        token(aud="another-client"),
        token(iss="https://example.com/pool"),
        token(exp=int(time.time()) - 60),
        token(token_use="access"),
        "not-a-jwt",
    ],
    ids=["wrong-key", "wrong-audience", "wrong-issuer", "expired", "access-token", "garbage"],
)
def test_invalid_tokens_are_rejected(client: TestClient, bad: str):
    assert client.get("/api/meetings", headers=bearer(bad)).status_code == 401


def test_first_request_creates_the_user(client: TestClient):
    me = client.get("/api/me", headers=bearer(token())).json()
    assert me["email"] == "anna@example.com"
    assert me["name"] == "Anna"
    assert client.get("/api/me", headers=bearer(token())).json()["id"] == me["id"]


def test_user_can_update_their_name(client: TestClient):
    response = client.patch("/api/me", json={"name": "Anna K."}, headers=bearer(token()))
    assert response.status_code == 200
    assert client.get("/api/me", headers=bearer(token())).json()["name"] == "Anna K."


def test_meetings_are_personal(client: TestClient):
    anna, oleh = bearer(token()), bearer(token(sub="sub-oleh", email="oleh@example.com"))
    meeting = client.post("/api/meetings", json=meeting_payload(), headers=anna).json()
    assert meeting["owner_id"] == client.get("/api/me", headers=anna).json()["id"]

    assert [m["id"] for m in client.get("/api/meetings", headers=anna).json()] == [meeting["id"]]
    assert client.get("/api/meetings", headers=oleh).json() == []

    url = f"/api/meetings/{meeting['id']}"
    assert client.get(url, headers=oleh).status_code == 404
    assert client.put(url, json=meeting_payload(title="Hijack"), headers=oleh).status_code == 404
    assert client.delete(url, headers=oleh).status_code == 404
    assert client.get(url, headers=anna).json()["title"] == "Sprint planning"
