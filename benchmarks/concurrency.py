"""Seeded local concurrency benchmark; dry-run only, not PX4 flight evidence."""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import lineguard.database as database  # noqa: E402
import lineguard.execution as execution  # noqa: E402
from lineguard.database import initialize_lineguard  # noqa: E402
from lineguard.models import UAVBackend  # noqa: E402
from lineguard.parser import parse_inspection_task  # noqa: E402
from lineguard.planner import generate_uav_mission_plan  # noqa: E402
from lineguard.repository import create_task  # noqa: E402


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


async def main() -> None:
    missions = 20
    requests_per_mission = 5
    with tempfile.TemporaryDirectory() as temp:
        runtime = Path(temp)
        os.environ["LINEGUARD_DATA_DIR"] = temp
        os.environ["LINEGUARD_DATABASE_URL"] = "sqlite:///" + (runtime / "tasks.db").as_posix()
        os.environ["LINEGUARD_DRYRUN_REALTIME"] = "false"
        os.environ["LINEGUARD_DUPLICATE_WAIT_TIMEOUT_S"] = "10"
        os.environ["LINEGUARD_USE_LLM"] = "false"
        initialize_lineguard()

        plans = []
        for index in range(missions):
            text = f"对 220kV 输电线路 {index + 1} 号到 {index + 3} 号塔执行强风巡检"
            task = create_task(text, [])
            plan = generate_uav_mission_plan(task.id, parse_inspection_task(task.id, text))
            execution.approve_mission(task.id, plan, "benchmark", UAVBackend.DRYRUN)
            plans.append((task.id, plan))

        original = execution._execute_uav_mission_once
        underlying_calls = 0

        async def counted(*args, **kwargs):
            nonlocal underlying_calls
            underlying_calls += 1
            await asyncio.sleep(0.02)
            return await original(*args, **kwargs)

        execution._execute_uav_mission_once = counted

        async def request(task_id, plan):
            started = time.perf_counter()
            result = await execution.execute_uav_mission(task_id, plan, UAVBackend.DRYRUN)
            return {
                "task_id": task_id,
                "status": result.status.value,
                "reused": result.reused_result,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "failure_reason": result.failure_reason,
            }

        try:
            rows = await asyncio.gather(
                *(
                    request(task_id, plan)
                    for task_id, plan in plans
                    for _ in range(requests_per_mission)
                )
            )
        finally:
            execution._execute_uav_mission_once = original
            if database._engine is not None:
                database._engine.dispose()

    latencies = [row["latency_ms"] for row in rows]
    output = {
        "scope": __doc__,
        "configuration": {
            "missions": missions,
            "requests_per_mission": requests_per_mission,
            "total_requests": len(rows),
            "uavs_per_mission": 3,
        },
        "summary": {
            "completed_requests": sum(row["status"] == "completed" for row in rows),
            "failed_requests": sum(row["status"] != "completed" for row in rows),
            "underlying_mission_executions": underlying_calls,
            "coalesced_or_cached_requests": sum(row["reused"] for row in rows),
            "duplicate_underlying_executions": max(0, underlying_calls - missions),
            "latency_ms": {
                "mean": round(statistics.fmean(latencies), 3),
                "p50": round(statistics.median(latencies), 3),
                "p95": round(percentile(latencies, 0.95), 3),
            },
        },
        "requests": rows,
    }
    target = ROOT / "benchmarks/results/concurrency.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"], ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
