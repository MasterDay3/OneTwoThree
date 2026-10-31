import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Table, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

meeting_participants = Table(
    "meeting_participants",
    Base.metadata,
    Column("meeting_id", Uuid, ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True),
    Column("participant_id", Uuid, ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_meeting_participants_participant_id", "participant_id"),
)


class User(Base):
    """An account from Cognito, keyed by its `sub`; holds what the app knows beyond the token."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    cognito_sub: Mapped[str] = mapped_column(String(128), unique=True)
    email: Mapped[str] = mapped_column(String(254))
    name: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    call_link: Mapped[str | None] = mapped_column(String(2048))
    place: Mapped[str | None] = mapped_column(String(255))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Each meeting belongs to one user and only they see it. Null only for meetings created
    # before accounts existed; nobody sees those.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # {"items": [...], "action_items": [...]} as saved by the user (PUT /api/meetings/{id}/agenda).
    agenda: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # What the LLM parser suggested for it, with the model and prompt version: the difference between
    # this and `agenda` is the parser's quality signal. Never shown back; read for evaluation only.
    agenda_ai_suggestion: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    participants: Mapped[list["Participant"]] = relationship(
        secondary=meeting_participants,
        back_populates="meetings",
        order_by="Participant.name",
        passive_deletes=True,
    )


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    meetings: Mapped[list[Meeting]] = relationship(
        secondary=meeting_participants,
        back_populates="participants",
        passive_deletes=True,
    )
