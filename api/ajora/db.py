import os
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import JSON, Boolean, Integer, LargeBinary, String, Text


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class Institute(Base):
    __tablename__ = "institutes"
    id: Mapped[str] = mapped_column(String(45), primary_key=True)
    name: Mapped[str] = mapped_column(String(180))
    category: Mapped[str] = mapped_column(String(30), index=True)
    city: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(80))
    domains: Mapped[list] = mapped_column(JSON)
    registry_url: Mapped[str] = mapped_column(Text)


class Source(Base):
    __tablename__ = "institute_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    institute_id: Mapped[str] = mapped_column(ForeignKey("institutes.id"))
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    permission_note: Mapped[str] = mapped_column(Text, default="")
    interval_hours: Mapped[int] = mapped_column(default=24)
    last_checked: Mapped[str | None] = mapped_column(String(32))
    last_success: Mapped[str | None] = mapped_column(String(32))
    failures: Mapped[int] = mapped_column(default=0)
    etag: Mapped[str | None] = mapped_column(Text)
    modified: Mapped[str | None] = mapped_column(Text)


class Snapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (UniqueConstraint("source_id", "digest"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("institute_sources.id"))
    digest: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[str] = mapped_column(String(32), default=now)
    body: Mapped[bytes] = mapped_column(LargeBinary)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_event_public_start", "status", "starts"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    institute_id: Mapped[str] = mapped_column(ForeignKey("institutes.id"))
    status: Mapped[str] = mapped_column(String(25), default="pending_verification")
    starts: Mapped[str] = mapped_column(String(10))
    published: Mapped[dict | None] = mapped_column(JSON)
    checked_at: Mapped[str | None] = mapped_column(String(32))
    demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Review(Base):
    __tablename__ = "review_queue"
    __table_args__ = (UniqueConstraint("dedupe_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"))
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("source_snapshots.id"))
    dedupe_key: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    proposal: Mapped[dict] = mapped_column(JSON)
    context: Mapped[str] = mapped_column(Text, default="")
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), default=now)
    resolved_at: Mapped[str | None] = mapped_column(String(32))


class Evidence(Base):
    __tablename__ = "field_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("review_queue.id"))
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshots.id"))
    field: Mapped[str] = mapped_column(String(100))
    excerpt: Mapped[str] = mapped_column(Text)


class Change(Base):
    __tablename__ = "event_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("review_queue.id"))
    previous: Mapped[dict] = mapped_column(JSON)
    current: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(32), default=now)


class Student(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(default=False)
    verification_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    verification_expiry: Mapped[str | None] = mapped_column(String(32))
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)


class LoginSession(Base):
    __tablename__ = "student_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    expires_at: Mapped[str] = mapped_column(String(32))


class FeedbackPost(Base):
    __tablename__ = "feedback_posts"
    __table_args__ = (Index("ix_feedback_parent_created", "parent_id", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("feedback_posts.id", ondelete="CASCADE"))
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"), index=True)
    title: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), default=now)
    removed: Mapped[bool] = mapped_column(default=False)


class Saved(Base):
    __tablename__ = "saved_events"
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("student_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    key: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(String(32), default=now)


class Run(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("institute_sources.id"), index=True)
    outcome: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(32), default=now)


class SiteCounter(Base):
    __tablename__ = "site_counters"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    total: Mapped[int] = mapped_column(default=0)


def make_engine(url: str):
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_setup(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    return engine


engine = make_engine(os.getenv("DATABASE_URL", "sqlite:///./ajora.db"))
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session
