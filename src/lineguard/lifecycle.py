"""Deterministic task lifecycle rules.

The LangGraph state describes orchestration progress, while this module protects
the durable database record from stale or concurrent HTTP requests.
"""

from __future__ import annotations

from lineguard.models import TaskStatus


class InvalidTaskTransition(RuntimeError):
    """Raised when a caller tries to skip or replay a lifecycle step."""


TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.REJECTED,
    TaskStatus.FAILED,
}

ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.PLANNING, TaskStatus.FAILED},
    TaskStatus.PLANNING: {TaskStatus.AWAITING_PLAN_APPROVAL, TaskStatus.FAILED},
    TaskStatus.AWAITING_PLAN_APPROVAL: {
        TaskStatus.PLAN_REVIEW_IN_PROGRESS,
        TaskStatus.FAILED,
    },
    TaskStatus.PLAN_REVIEW_IN_PROGRESS: {
        TaskStatus.EXECUTING,
        TaskStatus.REJECTED,
        TaskStatus.FAILED,
    },
    TaskStatus.EXECUTING: {TaskStatus.ANALYZING, TaskStatus.FAILED},
    TaskStatus.ANALYZING: {TaskStatus.AWAITING_REPORT_APPROVAL, TaskStatus.FAILED},
    TaskStatus.AWAITING_REPORT_APPROVAL: {
        TaskStatus.REPORT_REVIEW_IN_PROGRESS,
        TaskStatus.FAILED,
    },
    TaskStatus.REPORT_REVIEW_IN_PROGRESS: {
        TaskStatus.COMPLETED,
        TaskStatus.REJECTED,
        TaskStatus.FAILED,
    },
    TaskStatus.COMPLETED: set(),
    TaskStatus.REJECTED: set(),
    TaskStatus.FAILED: set(),
}


def validate_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTaskTransition(f"Illegal task transition: {current.value} -> {target.value}")
