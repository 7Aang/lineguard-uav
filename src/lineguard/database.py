from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from lineguard.config import ensure_runtime_directories


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class AssetRecord(Base):
    __tablename__ = "lineguard_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    asset_type: Mapped[str] = mapped_column(String(20), index=True)
    storage_path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="stored")
    calibration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TaskRecord(Base):
    __tablename__ = "lineguard_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    prompt: Mapped[str] = mapped_column(Text)
    asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(40), index=True)
    current_stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    task_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    mission_plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    mission_execution: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    video_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    risk_assessment: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    report_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ToolCallRecord(Base):
    __tablename__ = "lineguard_tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30))
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewRecord(Base):
    __tablename__ = "lineguard_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    stage: Mapped[str] = mapped_column(String(20))
    decision: Mapped[str] = mapped_column(String(20))
    reviewer: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReportRecord(Base):
    __tablename__ = "lineguard_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    summary: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(30))
    json_path: Mapped[str] = mapped_column(Text)
    markdown_path: Mapped[str] = mapped_column(Text)
    html_path: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TraceEventRecord(Base):
    __tablename__ = "lineguard_trace_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30))
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_engine = None
_session_factory = None


def initialize_lineguard() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    settings = ensure_runtime_directories()
    alembic_path = Path.cwd() / "alembic.ini"
    if alembic_path.exists():
        alembic_config = Config(str(alembic_path))
        alembic_config.set_main_option("sqlalchemy.url", settings.database_url)
        alembic_config.attributes["configure_logger"] = False
        command.upgrade(alembic_config, "head")
    connect_args = (
        {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    )
    _engine = create_engine(settings.database_url, connect_args=connect_args)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    if not alembic_path.exists():
        Base.metadata.create_all(_engine)


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        initialize_lineguard()
    return _session_factory


def new_id() -> str:
    return str(uuid4())
