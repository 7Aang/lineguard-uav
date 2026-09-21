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

    def uncertain(self, task_id: str, reason: str):
        with self.connection() as conn:
            conn.execute(
                "UPDATE executions SET state='uncertain',reason=?,updated=? WHERE task_id=? AND state='running'",
                (reason, time.time(), task_id),
            )
            self.event(conn, task_id, "uncertain", reason)

    def inspect(self, task_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM executions WHERE task_id=?", (task_id,)).fetchone()
            return dict(row) if row else None
