from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

from app.config import settings


def _iam_token(host: str, port: int, user: str, region: str) -> str:
    import boto3  # part of the Lambda runtime; only needed with DB_IAM_AUTH

    return boto3.client("rds", region_name=region).generate_db_auth_token(
        DBHostname=host, Port=port, DBUsername=user, Region=region
    )


def create_db_engine(**kwargs: Any) -> Engine:
    db_engine = create_engine(settings.sqlalchemy_url, **kwargs)
    if settings.db_iam_auth:
        # Tokens expire after 15 minutes, so each new connection gets a fresh one.
        @event.listens_for(db_engine, "do_connect")
        def use_iam_token(dialect: Any, conn_rec: Any, cargs: Any, cparams: dict[str, Any]) -> None:
            cparams["password"] = _iam_token(
                settings.db_host, settings.db_port, settings.db_user, settings.aws_region
            )
            cparams["sslmode"] = "require"

    return db_engine


engine = create_db_engine(
    pool_pre_ping=True,
    poolclass=NullPool if settings.db_null_pool else QueuePool,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
