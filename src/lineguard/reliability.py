"""Durable approval/dispatch ledger. Unknown outcomes are never blindly retried.

This is a single-host SQLite execution guard, not hardware exactly-once delivery.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class DispatchDenied(RuntimeError):
    pass


def contract_hash(plan: dict[str, Any], backend: str, limits: dict[str, Any]) -> str:
    payload = {"plan": plan, "backend": backend, "limits": limits, "policy": "execution-v2"}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class ExecutionLedger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS executions(
                  task_id TEXT PRIMARY KEY, contract TEXT NOT NULL, state TEXT NOT NULL,
                  reviewer TEXT NOT NULL, expires REAL NOT NULL, updated REAL NOT NULL,
                  result TEXT, reason TEXT);
                CREATE TABLE IF NOT EXISTS events(
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                  state TEXT NOT NULL, timestamp REAL NOT NULL, detail TEXT);
                CREATE TABLE IF NOT EXISTS reconciliations(
                  task_id TEXT PRIMARY KEY, contract TEXT NOT NULL,
                  resolution TEXT NOT NULL, reviewer TEXT NOT NULL,
                  evidence TEXT NOT NULL, evidence_hash TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS recovery_links(
                  source_task_id TEXT PRIMARY KEY, child_task_id TEXT UNIQUE NOT NULL,
                  reviewer TEXT NOT NULL, created REAL NOT NULL);
            """)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def event(conn, task_id: str, state: str, detail: str = ""):
        conn.execute(
            "INSERT INTO events(task_id,state,timestamp,detail) VALUES(?,?,?,?)",
            (task_id, state, time.time(), detail),
        )

    def approve(self, task_id: str, contract: str, reviewer: str, ttl: float = 600):
        if not reviewer.strip() or not 0 < ttl <= 3600:
            raise ValueError("Reviewer and approval TTL (0,3600] required")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM executions WHERE task_id=?", (task_id,)).fetchone()
            if old and old["state"] != "approved":
                if old["contract"] == contract and old["state"] == "completed":
                    return  # A duplicate approval cannot authorize another dispatch.
                raise DispatchDenied("A dispatched task requires reconciliation or a new task ID")
            conn.execute(
                "INSERT OR REPLACE INTO executions VALUES(?,?,?,?,?,?,NULL,NULL)",
                (task_id, contract, "approved", reviewer, time.time() + ttl, time.time()),
            )
            self.event(conn, task_id, "approved", contract)

    def claim(self, task_id: str, contract: str) -> dict | None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM executions WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                raise DispatchDenied("APPROVAL_REQUIRED")
            if row["contract"] != contract:
                raise DispatchDenied("APPROVED_CONTRACT_CHANGED")
            if row["state"] == "completed":
                self.event(conn, task_id, "result_reused")
                return json.loads(row["result"])
            if row["state"] != "approved":
                raise DispatchDenied("OUTCOME_NOT_CONFIRMED: no automatic redispatch")
            if row["expires"] < time.time():
                raise DispatchDenied("APPROVAL_EXPIRED")
            conn.execute(
                "UPDATE executions SET state='running',updated=? WHERE task_id=?",
                (time.time(), task_id),
            )
            self.event(conn, task_id, "running")
            return None

    def finish(self, task_id: str, result: dict):
        state = "completed" if result["status"] == "completed" else "uncertain"
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE executions SET state=?,result=?,updated=? WHERE task_id=? AND state='running'",
                (state, json.dumps(result, allow_nan=False), time.time(), task_id),
            )
            if cursor.rowcount != 1:
                raise DispatchDenied("Illegal terminal state transition")
            self.event(conn, task_id, state)

    def wait_for_terminal(
        self,
        task_id: str,
        contract: str,
        timeout_s: float,
        poll_s: float = 0.01,
    ) -> dict:
        """Wait for an in-flight duplicate and return its committed result.

        This only coalesces requests while the original executor is running.  It
        never changes an unknown outcome or authorizes another dispatch.
        """

        if timeout_s <= 0 or poll_s <= 0:
            raise ValueError("Positive timeout and poll interval required")
        deadline = time.monotonic() + timeout_s
        while True:
            row = self.inspect(task_id)
            if row is None:
                raise DispatchDenied("APPROVAL_REQUIRED")
            if row["contract"] != contract:
                raise DispatchDenied("APPROVED_CONTRACT_CHANGED")
            if row["state"] == "completed":
                with self.connection() as conn:
                    self.event(conn, task_id, "concurrent_result_reused")
                return json.loads(row["result"])
            if row["state"] != "running":
                raise DispatchDenied("OUTCOME_NOT_CONFIRMED: no automatic redispatch")
            if time.monotonic() >= deadline:
                raise DispatchDenied("EXECUTION_IN_PROGRESS: duplicate wait timed out")
            time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))

    def uncertain(self, task_id: str, reason: str):
        with self.connection() as conn:
            cursor = conn.execute(
                "UPDATE executions SET state='uncertain',reason=?,updated=? WHERE task_id=? AND state='running'",
                (reason, time.time(), task_id),
            )
            if cursor.rowcount:
                self.event(conn, task_id, "uncertain", reason)

    def reconcile(
        self,
        task_id: str,
        contract: str,
        reviewer: str,
        resolution: str,
        evidence: str,
        executor_stopped: bool,
    ):
        """Operator attestation, NOT automatic verification of hardware telemetry.

        The old ID stays terminal. A new mission must be independently approved.
        """
        if resolution not in {"not_executed", "aborted", "completed"}:
            raise ValueError("Invalid resolution")
        if not reviewer.strip() or len(evidence.strip()) < 20 or executor_stopped is not True:
            raise ValueError(
                "Reviewer, evidence (20+ chars), and stopped-executor attestation required"
            )
        digest = hashlib.sha256(evidence.encode()).hexdigest()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM executions WHERE task_id=?", (task_id,)).fetchone()
            if not row or row["contract"] != contract:
                raise DispatchDenied("RECONCILIATION_CONTRACT_MISMATCH")
            previous = conn.execute(
                "SELECT * FROM reconciliations WHERE task_id=?", (task_id,)
            ).fetchone()
            if previous:
                if (previous["resolution"], previous["reviewer"], previous["evidence_hash"]) == (
                    resolution,
                    reviewer,
                    digest,
                ):
                    return dict(previous)
                raise DispatchDenied("ALREADY_RECONCILED")
            if row["state"] not in {"running", "uncertain"}:
                raise DispatchDenied("RECONCILIATION_REQUIRES_UNKNOWN_OUTCOME")
            conn.execute(
                "INSERT INTO reconciliations VALUES(?,?,?,?,?,?,?)",
                (task_id, contract, resolution, reviewer, evidence, digest, time.time()),
            )
            conn.execute(
                "UPDATE executions SET state='reconciled',updated=? WHERE task_id=?",
                (time.time(), task_id),
            )
            self.event(
                conn,
                task_id,
                "reconciled",
                json.dumps(
                    {"reviewer": reviewer, "resolution": resolution, "evidence_hash": digest}
                ),
            )
            return dict(
                conn.execute("SELECT * FROM reconciliations WHERE task_id=?", (task_id,)).fetchone()
            )

    def reserve_recovery(self, source_task_id: str, reviewer: str) -> str:
        """Durable outbox ID: retrying after a crash reuses the same child task."""
        from uuid import uuid4

        if not reviewer.strip():
            raise ValueError("Reviewer required")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            resolution = conn.execute(
                "SELECT resolution FROM reconciliations WHERE task_id=?", (source_task_id,)
            ).fetchone()
            if not resolution or resolution["resolution"] not in {"not_executed", "aborted"}:
                raise DispatchDenied("RECOVERY_REQUIRES_RECONCILED_RETRYABLE_OUTCOME")
            old = conn.execute(
                "SELECT child_task_id FROM recovery_links WHERE source_task_id=?", (source_task_id,)
            ).fetchone()
            if old:
                return old["child_task_id"]
            child = str(uuid4())
            conn.execute(
                "INSERT INTO recovery_links VALUES(?,?,?,?)",
                (source_task_id, child, reviewer, time.time()),
            )
            self.event(conn, source_task_id, "recovery_reserved", child)
            return child

    def history(self, task_id: str) -> list[dict]:
        with self.connection() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM events WHERE task_id=? ORDER BY sequence", (task_id,)
                )
            ]

    def inspect(self, task_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM executions WHERE task_id=?", (task_id,)).fetchone()
            return dict(row) if row else None
