from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select, update

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
from lineguard.lifecycle import InvalidTaskTransition, validate_transition
from lineguard.models import TaskStatus


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
    if "status" in values:
        raise ValueError("Use transition_task() for durable status changes")
    with get_session_factory()() as session:
        record = session.get(TaskRecord, task_id)
        if record is None:
            raise KeyError(task_id)
        for key, value in values.items():
            setattr(record, key, value)
        record.updated_at = utcnow()
        session.commit()
        return record


def transition_task(
    task_id: str,
    target: TaskStatus,
    *,
    expected: TaskStatus | set[TaskStatus] | None = None,
    expected_stage: str | None = None,
    current_stage: str | None = None,
    **values: Any,
) -> TaskRecord:
    """Atomically compare and swap a task status and record the transition.

    The status predicate protects the workflow from stale API requests.  The
    lifecycle event is committed in the same transaction as the task update.
    """

    if "status" in values:
        raise ValueError("status is controlled by transition_task()")
    with get_session_factory()() as session:
        record = session.get(TaskRecord, task_id)
        if record is None:
            raise KeyError(task_id)
        current = TaskStatus(record.status)
        expected_set = {expected} if isinstance(expected, TaskStatus) else expected
        if expected_set is not None and current not in expected_set:
            raise InvalidTaskTransition(
                f"Stale task status: expected {sorted(x.value for x in expected_set)}, "
                f"found {current.value}"
            )
        if expected_stage is not None and record.current_stage != expected_stage:
            raise InvalidTaskTransition(
                f"Stale task stage: expected {expected_stage}, found {record.current_stage}"
            )
        validate_transition(current, target)

        predicates = [TaskRecord.id == task_id, TaskRecord.status == current.value]
        if expected_stage is not None:
            predicates.append(TaskRecord.current_stage == expected_stage)
        update_values = {
            **values,
            "status": target.value,
            "updated_at": utcnow(),
        }
        if current_stage is not None:
            update_values["current_stage"] = current_stage
        result = session.execute(update(TaskRecord).where(*predicates).values(**update_values))
        if result.rowcount != 1:
            session.rollback()
            raise InvalidTaskTransition("Concurrent task transition detected")

        max_sequence = session.scalar(
            select(func.max(TraceEventRecord.sequence)).where(TraceEventRecord.task_id == task_id)
        )
        session.add(
            TraceEventRecord(
                id=new_id(),
                task_id=task_id,
                sequence=(max_sequence or 0) + 1,
                event_type="lifecycle",
                name=f"{current.value}->{target.value}",
                status="committed",
                input_summary={"from": current.value},
                output_summary={"to": target.value, "stage": current_stage},
            )
        )
        session.commit()
        return session.get(TaskRecord, task_id)


def claim_review(task_id: str, stage: str, **values: Any) -> TaskRecord:
    """Persist one review and atomically fence concurrent duplicate reviews."""

    mapping = {
        "plan": (
            TaskStatus.AWAITING_PLAN_APPROVAL,
            TaskStatus.PLAN_REVIEW_IN_PROGRESS,
        ),
        "report": (
            TaskStatus.AWAITING_REPORT_APPROVAL,
            TaskStatus.REPORT_REVIEW_IN_PROGRESS,
        ),
    }
    if stage not in mapping:
        raise ValueError(f"Unknown review stage: {stage}")
    expected, target = mapping[stage]
    with get_session_factory()() as session:
        result = session.execute(
            update(TaskRecord)
            .where(TaskRecord.id == task_id, TaskRecord.status == expected.value)
            .values(
                status=target.value,
                current_stage=f"{stage}_review_in_progress",
                updated_at=utcnow(),
            )
        )
        if result.rowcount != 1:
            session.rollback()
            record = session.get(TaskRecord, task_id)
            if record is None:
                raise KeyError(task_id)
            raise InvalidTaskTransition(
                f"Task is not available for {stage} review; current status is {record.status}"
            )
        session.add(ReviewRecord(id=new_id(), task_id=task_id, stage=stage, **values))
        max_sequence = session.scalar(
            select(func.max(TraceEventRecord.sequence)).where(TraceEventRecord.task_id == task_id)
        )
        session.add(
            TraceEventRecord(
                id=new_id(),
                task_id=task_id,
                sequence=(max_sequence or 0) + 1,
                event_type="lifecycle",
                name=f"claim_{stage}_review",
                status="committed",
                input_summary={"from": expected.value},
                output_summary={"to": target.value},
            )
        )
        session.commit()
        return session.get(TaskRecord, task_id)


def operational_metrics() -> dict[str, Any]:
    """Return database-backed service counters without external telemetry."""

    with get_session_factory()() as session:
        status_rows = session.execute(
            select(TaskRecord.status, func.count()).group_by(TaskRecord.status)
        ).all()
        total_events = session.scalar(select(func.count()).select_from(TraceEventRecord)) or 0
        failed_events = (
            session.scalar(
                select(func.count())
                .select_from(TraceEventRecord)
                .where(TraceEventRecord.status == "failed")
            )
            or 0
        )
        average_duration = session.scalar(
            select(func.avg(TraceEventRecord.duration_ms)).where(
                TraceEventRecord.duration_ms.is_not(None)
            )
        )
        return {
            "tasks_total": sum(count for _, count in status_rows),
            "tasks_by_status": {status: count for status, count in status_rows},
            "trace_events_total": total_events,
            "trace_failures_total": failed_events,
            "average_trace_duration_ms": (
                round(float(average_duration), 3) if average_duration is not None else None
            ),
        }


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
