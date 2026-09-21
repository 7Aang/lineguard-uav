from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select

from lineguard.database import (
    AssetRecord,
    ReportRecord,
    ReviewRecord,
    TaskRecord,
    ToolCallRecord,
    TraceEventRecord,
    get_session_factory,
    new_id,
    utcnow,
)


def create_asset(**values: Any) -> AssetRecord:
    with get_session_factory()() as session:
        record = AssetRecord(id=new_id(), **values)
        session.add(record)
        session.commit()
        return record


def get_asset(asset_id: str) -> AssetRecord | None:
    with get_session_factory()() as session:
        return session.get(AssetRecord, asset_id)


def get_assets(asset_ids: Iterable[str]) -> list[AssetRecord]:
    ids = list(asset_ids)
    if not ids:
        return []
    with get_session_factory()() as session:
        return list(session.scalars(select(AssetRecord).where(AssetRecord.id.in_(ids))))


def update_asset(asset_id: str, **values: Any) -> AssetRecord:
    with get_session_factory()() as session:
        record = session.get(AssetRecord, asset_id)
        if record is None:
            raise KeyError(asset_id)
        for key, value in values.items():
            setattr(record, key, value)
        session.commit()
        return record


def create_task(prompt: str, asset_ids: list[str]) -> TaskRecord:
    with get_session_factory()() as session:
        record = TaskRecord(
            id=new_id(),
            prompt=prompt,
            asset_ids=asset_ids,
            status="created",
            current_stage="created",
        )
        session.add(record)
        session.commit()
        return record


def get_task(task_id: str) -> TaskRecord | None:
    with get_session_factory()() as session:
        return session.get(TaskRecord, task_id)


def update_task(task_id: str, **values: Any) -> TaskRecord:
    with get_session_factory()() as session:
        record = session.get(TaskRecord, task_id)
        if record is None:
            raise KeyError(task_id)
        for key, value in values.items():
            setattr(record, key, value)
        record.updated_at = utcnow()
        session.commit()
        return record


def add_tool_call(task_id: str, tool_name: str, status: str, **values: Any) -> ToolCallRecord:
    with get_session_factory()() as session:
        record = ToolCallRecord(
            id=new_id(),
            task_id=task_id,
            tool_name=tool_name,
            status=status,
            **values,
        )
        session.add(record)
        session.commit()
        return record


def add_review(task_id: str, **values: Any) -> ReviewRecord:
    with get_session_factory()() as session:
        record = ReviewRecord(id=new_id(), task_id=task_id, **values)
        session.add(record)
        session.commit()
        return record


def create_report(task_id: str, **values: Any) -> ReportRecord:
    with get_session_factory()() as session:
        existing = session.scalar(select(ReportRecord).where(ReportRecord.task_id == task_id))
        if existing is not None:
            for key, value in values.items():
                setattr(existing, key, value)
            session.commit()
            return existing
        record = ReportRecord(id=new_id(), task_id=task_id, **values)
        session.add(record)
        session.commit()
        return record


def get_report(report_id: str) -> ReportRecord | None:
    with get_session_factory()() as session:
        return session.get(ReportRecord, report_id)


def add_trace(
    task_id: str,
    event_type: str,
    name: str,
    status: str,
    **values: Any,
) -> TraceEventRecord:
    with get_session_factory()() as session:
        max_sequence = session.scalar(
            select(func.max(TraceEventRecord.sequence)).where(TraceEventRecord.task_id == task_id)
        )
        record = TraceEventRecord(
            id=new_id(),
            task_id=task_id,
            sequence=(max_sequence or 0) + 1,
            event_type=event_type,
            name=name,
            status=status,
            **values,
        )
        session.add(record)
        session.commit()
        return record


def list_trace(task_id: str) -> list[TraceEventRecord]:
    with get_session_factory()() as session:
        return list(
            session.scalars(
                select(TraceEventRecord)
                .where(TraceEventRecord.task_id == task_id)
                .order_by(TraceEventRecord.sequence)
            )
        )
