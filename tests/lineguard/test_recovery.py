from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from lineguard.recovery import create_recovery_draft, ledger
from lineguard.reliability import DispatchDenied, ExecutionLedger
from lineguard.repository import create_task
from service import app

EVIDENCE = "Test operator checked persisted dispatch log and confirmed executor stopped."


def unknown(execution_ledger, task="task"):
    execution_ledger.approve(task, "a" * 64, "operator")
    execution_ledger.claim(task, "a" * 64)


def test_reconciliation_requires_attestation_and_correct_contract(tmp_path):
    execution_ledger = ExecutionLedger(tmp_path / "ledger.db")
    unknown(execution_ledger)
    with pytest.raises(ValueError):
        execution_ledger.reconcile("task", "a" * 64, "operator", "aborted", EVIDENCE, False)
    with pytest.raises(DispatchDenied, match="CONTRACT"):
        execution_ledger.reconcile("task", "b" * 64, "operator", "aborted", EVIDENCE, True)
    assert execution_ledger.inspect("task")["state"] == "running"


def test_reconciliation_is_idempotent_and_fences_late_completion(tmp_path):
    execution_ledger = ExecutionLedger(tmp_path / "ledger.db")
    unknown(execution_ledger)
    first = execution_ledger.reconcile("task", "a" * 64, "operator", "aborted", EVIDENCE, True)
    assert (
        execution_ledger.reconcile("task", "a" * 64, "operator", "aborted", EVIDENCE, True) == first
    )
    with pytest.raises(DispatchDenied):
        execution_ledger.finish("task", {"status": "completed"})
    execution_ledger.uncertain("task", "late failure")
    assert execution_ledger.inspect("task")["state"] == "reconciled"
    assert len([e for e in execution_ledger.history("task") if e["state"] == "reconciled"]) == 1
    with pytest.raises(DispatchDenied):
        execution_ledger.claim("task", "a" * 64)


def test_completed_reconciliation_cannot_authorize_reexecution(tmp_path):
    execution_ledger = ExecutionLedger(tmp_path / "ledger.db")
    unknown(execution_ledger)
    execution_ledger.reconcile("task", "a" * 64, "operator", "completed", EVIDENCE, True)
    with pytest.raises(DispatchDenied):
        execution_ledger.reserve_recovery("task", "operator")


def test_concurrent_recovery_and_outbox_restart(lineguard_runtime):
    source = create_task("巡检 220kV 3号到5号塔强风导线舞动", [])
    execution_ledger = ledger()
    unknown(execution_ledger, source.id)
    execution_ledger.reconcile(source.id, "a" * 64, "operator", "not_executed", EVIDENCE, True)
    # Crash boundary: reserve committed, no child TaskRecord exists yet.
    reserved = execution_ledger.reserve_recovery(source.id, "operator")
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: create_recovery_draft(source.id, "operator").id, range(16)))
    assert set(ids) == {reserved}
    assert create_recovery_draft(source.id, "operator").status == "created"
    assert ledger().inspect(reserved) is None  # Creating a draft cannot authorize dispatch.


def test_http_recovery_requires_fresh_approval(lineguard_runtime):
    source = create_task("巡检 220kV 3号到5号塔强风导线舞动", [])
    unknown(ledger(), source.id)
    client = TestClient(app)
    url = f"/api/tasks/{source.id}"
    assert client.post(url + "/recovery", json={"reviewer": "operator"}).status_code == 409
    payload = {
        "contract": "a" * 64,
        "reviewer": "operator",
        "resolution": "not_executed",
        "evidence": EVIDENCE,
        "executor_stopped": True,
    }
    assert client.post(url + "/reconcile", json=payload).status_code == 200
    child = client.post(url + "/recovery", json={"reviewer": "operator"}).json()
    assert child["status"] == "created"
    assert client.post(url + "/recovery", json={"reviewer": "operator"}).json()["id"] == child["id"]
    child_url = f"/api/tasks/{child['id']}"
    planned = client.post(child_url + "/start").json()
    assert planned["status"] == "awaiting_plan_approval"
    assert planned["mission_execution"] is None
    assert client.post(child_url + "/start").json()["status"] == "awaiting_plan_approval"
    executed = client.post(
        child_url + "/reviews",
        json={"stage": "plan", "decision": "approve", "reviewer": "new-reviewer"},
    ).json()
    assert executed["status"] == "awaiting_report_approval"
    assert executed["mission_execution"]["status"] == "completed"
    assert client.get(url + "/execution-ledger").json()["execution"]["state"] == "reconciled"
