from concurrent.futures import ThreadPoolExecutor

import pytest

from lineguard import repository
from lineguard.lifecycle import InvalidTaskTransition
from lineguard.models import TaskStatus


def _awaiting_plan_task():
    task = repository.create_task("inspect line", [])
    repository.transition_task(
        task.id,
        TaskStatus.PLANNING,
        expected=TaskStatus.CREATED,
        current_stage="task_parser",
    )
    repository.transition_task(
        task.id,
        TaskStatus.AWAITING_PLAN_APPROVAL,
        expected=TaskStatus.PLANNING,
        current_stage="plan_approval",
    )
    return task


def test_illegal_transition_is_rejected_and_terminal_state_is_fenced(lineguard_runtime):
    task = repository.create_task("inspect line", [])
    with pytest.raises(InvalidTaskTransition, match="Illegal"):
        repository.transition_task(task.id, TaskStatus.COMPLETED)

    repository.transition_task(task.id, TaskStatus.FAILED, expected=TaskStatus.CREATED)
    with pytest.raises(InvalidTaskTransition):
        repository.transition_task(task.id, TaskStatus.PLANNING)


def test_concurrent_review_claim_has_one_winner(lineguard_runtime):
    task = _awaiting_plan_task()

    def claim(index):
        try:
            repository.claim_review(
                task.id,
                "plan",
                decision="approve",
                reviewer=f"reviewer-{index}",
                reason=None,
            )
            return True
        except InvalidTaskTransition:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(claim, range(16)))

    assert sum(outcomes) == 1
    claimed = repository.get_task(task.id)
    assert claimed.status == TaskStatus.PLAN_REVIEW_IN_PROGRESS.value
    repository.transition_task(
        task.id,
        TaskStatus.EXECUTING,
        expected=TaskStatus.PLAN_REVIEW_IN_PROGRESS,
    )

    trace_names = [event.name for event in repository.list_trace(task.id)]
    assert "created->planning" in trace_names
    assert "planning->awaiting_plan_approval" in trace_names
    assert trace_names.count("claim_plan_review") == 1


def test_operational_metrics_are_database_backed(lineguard_runtime):
    task = repository.create_task("inspect line", [])
    repository.transition_task(
        task.id,
        TaskStatus.PLANNING,
        expected=TaskStatus.CREATED,
    )
    metrics = repository.operational_metrics()
    assert metrics["tasks_total"] == 1
    assert metrics["tasks_by_status"] == {"planning": 1}
    assert metrics["trace_events_total"] == 1
