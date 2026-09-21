"""Actual subprocess exits at ledger boundaries, with a synthetic dispatch log.

This is not PX4/hardware fault injection. 'Operator' resolution is a test fixture.
"""

import argparse
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lineguard.reliability import DispatchDenied, ExecutionLedger  # noqa: E402


def dispatch(root, task):
    with (root / "dispatch.jsonl").open("a") as f:
        f.write(json.dumps({"task": task}) + "\n")
        f.flush()
        os.fsync(f.fileno())


def worker(root, phase):
    execution_ledger = ExecutionLedger(root / "ledger.db")
    execution_ledger.approve("task", "a" * 64, "fixture")
    if phase == "before_claim":
        os._exit(17)
    execution_ledger.claim("task", "a" * 64)
    if phase == "after_claim":
        os._exit(17)
    dispatch(root, "task")
    if phase == "after_dispatch":
        os._exit(17)
    execution_ledger.finish("task", {"status": "completed"})
    os._exit(17)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker")
    parser.add_argument("--phase")
    args = parser.parse_args()
    if args.worker:
        worker(Path(args.worker), args.phase)
    rows = []
    spec = importlib.util.spec_from_file_location(
        "ledger_v1", ROOT / "benchmarks/baseline/ledger_v1.py"
    )
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    for phase in ["before_claim", "after_claim", "after_dispatch", "after_commit"]:
        for iteration in range(5):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                process = subprocess.run(
                    [sys.executable, __file__, "--worker", temp, "--phase", phase],
                    capture_output=True,
                    timeout=30,
                )
                assert process.returncode == 17
                execution_ledger = ExecutionLedger(root / "ledger.db")
                before = execution_ledger.inspect("task")["state"]
                source = sqlite3.connect(root / "ledger.db")
                copy = sqlite3.connect(root / "baseline.db")
                source.backup(copy)
                source.close()
                copy.close()
                try:
                    old.ExecutionLedger(root / "baseline.db").claim("task", "a" * 64)
                    baseline_blocked = False
                except old.DispatchDenied:
                    baseline_blocked = True
                resolution = None
                late_write_rejected = None
                try:
                    cached = execution_ledger.claim("task", "a" * 64)
                    if cached is None:
                        dispatch(root, "task")
                        execution_ledger.finish("task", {"status": "completed"})
                    recovered = True
                except DispatchDenied:
                    resolution = "not_executed" if phase == "after_claim" else "completed"
                    execution_ledger.reconcile(
                        "task",
                        "a" * 64,
                        "fixture-operator",
                        resolution,
                        "Fixture inspected the synthetic dispatch log after confirmed subprocess termination.",
                        True,
                    )
                    try:
                        execution_ledger.finish("task", {"status": "completed"})
                        late_write_rejected = False
                    except DispatchDenied:
                        late_write_rejected = True
                    if resolution == "not_executed":
                        child = execution_ledger.reserve_recovery("task", "fixture-operator")
                        assert (
                            execution_ledger.reserve_recovery("task", "fixture-operator") == child
                        )
                        # Explicit fresh approval before child dispatch.
                        execution_ledger.approve(child, "b" * 64, "new-reviewer")
                        execution_ledger.claim(child, "b" * 64)
                        dispatch(root, child)
                        execution_ledger.finish(child, {"status": "completed"})
                    recovered = True
                effects = [
                    json.loads(x) for x in (root / "dispatch.jsonl").read_text().splitlines()
                ]
                assert len(effects) == 1
                rows.append(
                    {
                        "phase": phase,
                        "iteration": iteration,
                        "subprocess_exit": process.returncode,
                        "state_after_crash": before,
                        "v1_blocked_without_reconciliation": baseline_blocked,
                        "operator_resolution_fixture": resolution,
                        "late_write_rejected": late_write_rejected,
                        "recovered": recovered,
                        "synthetic_dispatch_count": len(effects),
                    }
                )
    output = {
        "scope": __doc__,
        "cases": rows,
        "summary": {
            "cases": len(rows),
            "v1_recovery_flow_available": sum(
                not r["v1_blocked_without_reconciliation"] for r in rows
            ),
            "v2_recovery_flow_completed": sum(r["recovered"] for r in rows),
            "operator_assisted_cases": sum(
                r["operator_resolution_fixture"] is not None for r in rows
            ),
            "duplicate_synthetic_dispatches": sum(
                max(0, r["synthetic_dispatch_count"] - 1) for r in rows
            ),
        },
    }
    target = ROOT / "benchmarks/results/fault_recovery.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"]))


if __name__ == "__main__":
    main()
