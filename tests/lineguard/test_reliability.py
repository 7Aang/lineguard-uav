from concurrent.futures import ThreadPoolExecutor

import pytest

from lineguard.reliability import DispatchDenied, ExecutionLedger


def test_atomic_claim_under_concurrency(tmp_path):
    ledger = ExecutionLedger(tmp_path / "ledger.db")
    ledger.approve("task", "contract", "reviewer")

    def claim(_):
        try:
            ledger.claim("task", "contract")
            return True
        except DispatchDenied:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(claim, range(32))) == 1


def test_restart_reuses_completed_result(tmp_path):
    path = tmp_path / "ledger.db"
    ledger = ExecutionLedger(path)
    ledger.approve("task", "contract", "reviewer")
    assert ledger.claim("task", "contract") is None
    ledger.finish("task", {"status": "completed", "value": 42})
    assert ExecutionLedger(path).claim("task", "contract")["value"] == 42
    with pytest.raises(DispatchDenied, match="CONTRACT_CHANGED"):
        ledger.claim("task", "changed")


@pytest.mark.parametrize("state", ["running", "uncertain"])
def test_unknown_outcome_cannot_redispatch(tmp_path, state):
    ledger = ExecutionLedger(tmp_path / "ledger.db")
    ledger.approve("task", "contract", "reviewer")
    ledger.claim("task", "contract")
    if state == "uncertain":
        ledger.uncertain("task", "connection lost")
    with pytest.raises(DispatchDenied, match="OUTCOME_NOT_CONFIRMED"):
        ledger.claim("task", "contract")
    with pytest.raises(DispatchDenied):
        ledger.approve("task", "contract", "reviewer")


def test_expiry_and_missing_approval(tmp_path):
    ledger = ExecutionLedger(tmp_path / "ledger.db")
    with pytest.raises(DispatchDenied, match="APPROVAL_REQUIRED"):
        ledger.claim("task", "contract")
    ledger.approve("task", "contract", "reviewer")
    with ledger.connection() as conn:
        conn.execute("UPDATE executions SET expires=0")
    with pytest.raises(DispatchDenied, match="APPROVAL_EXPIRED"):
        ledger.claim("task", "contract")
