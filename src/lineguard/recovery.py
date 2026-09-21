"""Reconciliation and durable recovery drafts for the local trusted operator."""

from sqlalchemy.exc import IntegrityError

from lineguard import repository
from lineguard.config import get_settings
from lineguard.database import TaskRecord, get_session_factory
from lineguard.reliability import ExecutionLedger


def ledger():
    return ExecutionLedger(get_settings().data_dir / "execution_ledger.sqlite3")


def create_recovery_draft(source_task_id: str, reviewer: str):
    source = repository.get_task(source_task_id)
    if source is None:
        raise KeyError(source_task_id)
    child_id = ledger().reserve_recovery(source_task_id, reviewer)
    # The outbox ID is committed first. A crash before this insert is repaired by retry.
    with get_session_factory()() as session:
        old = session.get(TaskRecord, child_id)
        if old is not None:
            return old
        record = TaskRecord(
            id=child_id,
            prompt=source.prompt,
            asset_ids=source.asset_ids,
            status="created",
            current_stage="recovery_draft",
        )
        session.add(record)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return session.get(TaskRecord, child_id)
        return record
