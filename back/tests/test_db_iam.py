"""DB_IAM_AUTH: each new connection signs in with a fresh IAM token over TLS (Aurora express config)."""

from sqlalchemy.pool import NullPool

from app import db
from app.config import settings


def _run_connect_hooks(engine) -> dict:
    cparams: dict = {"host": settings.db_host, "user": settings.db_user}
    for hook in engine.dialect.dispatch.do_connect:
        hook(engine.dialect, None, [], cparams)
    return cparams


def test_iam_auth_sets_a_fresh_token_and_tls_per_connection(monkeypatch):
    monkeypatch.setattr(settings, "db_iam_auth", True)
    monkeypatch.setattr(settings, "db_host", "meetings-db.cluster-abc.us-east-1.rds.amazonaws.com")
    monkeypatch.setattr(settings, "db_port", 5432)
    monkeypatch.setattr(settings, "db_user", "postgres")
    monkeypatch.setattr(settings, "aws_region", "us-east-1")
    calls = []

    def fake_token(host, port, user, region):
        calls.append((host, port, user, region))
        return f"token-{len(calls)}"

    monkeypatch.setattr(db, "_iam_token", fake_token)
    engine = db.create_db_engine(poolclass=NullPool)

    first = _run_connect_hooks(engine)
    second = _run_connect_hooks(engine)

    assert first["password"] == "token-1"
    assert second["password"] == "token-2"
    assert first["sslmode"] == "require"
    assert calls[0] == ("meetings-db.cluster-abc.us-east-1.rds.amazonaws.com", 5432, "postgres", "us-east-1")


def test_password_auth_adds_no_connect_hook(monkeypatch):
    monkeypatch.setattr(settings, "db_iam_auth", False)
    engine = db.create_db_engine(poolclass=NullPool)

    assert "password" not in _run_connect_hooks(engine)


def test_cloud_sql_socket_directory_is_passed_as_the_host(monkeypatch):
    """Cloud Run mounts Cloud SQL as a unix socket under /cloudsql/<connection name>; psycopg takes
    the directory as the host, password sign-in, no TLS settings of ours (the connector encrypts)."""
    monkeypatch.setattr(settings, "database_url", None)
    monkeypatch.setattr(settings, "db_iam_auth", False)
    monkeypatch.setattr(settings, "db_host", "/cloudsql/spry-mykola-2026:us-central1:meetings-db")
    monkeypatch.setattr(settings, "db_password", "secret")
    engine = db.create_db_engine(poolclass=NullPool)

    _, cparams = engine.dialect.create_connect_args(engine.url)

    assert cparams["host"] == "/cloudsql/spry-mykola-2026:us-central1:meetings-db"
    assert cparams["password"] == "secret"
    assert "sslmode" not in cparams
