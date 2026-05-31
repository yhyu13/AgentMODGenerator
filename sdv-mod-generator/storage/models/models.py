"""SQLAlchemy ORM models for the SDV Mod Generator database."""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    ForeignKey,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discord_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    mod_requests: Mapped[list["ModRequest"]] = relationship(back_populates="user")
    mod_history: Mapped[list["ModHistory"]] = relationship(back_populates="user")


class ModRequest(Base):
    __tablename__ = "mod_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(64))
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(String(32), default="p1_shop_channel")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    generators: Mapped[list[Any]] = mapped_column(JSON, default=list)
    hint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped[User | None] = relationship(back_populates="mod_requests")
    output: Mapped["ModOutput | None"] = relationship(back_populates="request", uselist=False)
    history_entries: Mapped[list["ModHistory"]] = relationship(back_populates="request")


class ModOutput(Base):
    __tablename__ = "mod_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("mod_requests.request_id", ondelete="CASCADE"), unique=True
    )
    zip_key: Mapped[str | None] = mapped_column(String(512))
    zip_url: Mapped[str | None] = mapped_column(Text)
    files_preview: Mapped[list[str]] = mapped_column(JSON, default=list)
    t1_errors: Mapped[list[str]] = mapped_column(JSON, default=list)
    t2_feedback: Mapped[str | None] = mapped_column(Text)
    t2_score: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    request: Mapped[ModRequest] = relationship(back_populates="output")


class ModHistory(Base):
    __tablename__ = "mod_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("mod_requests.request_id", ondelete="SET NULL")
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    user: Mapped[User | None] = relationship(back_populates="mod_history")
    request: Mapped[ModRequest | None] = relationship(back_populates="history_entries")