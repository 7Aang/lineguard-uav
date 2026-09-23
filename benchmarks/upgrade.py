"""Seeded regression benchmark; synthetic trajectories, not PX4 flight results."""

import asyncio
import importlib.util
import json
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from provenance import benchmark_metadata

import lineguard.execution as upgraded
from lineguard.database import initialize_lineguard
from lineguard.models import MissionPlan, UAVAssignment, UAVBackend, Waypoint
from lineguard.parser import parse_inspection_task
from lineguard.planner import generate_uav_mission_plan
from lineguard.repository import create_task
from lineguard.safety import validate_uav_mission


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def point(x, y):
    return Waypoint(x_m=x, y_m=y, z_m=20, yaw_deg=0)


def scenario(kind, length, speed):
    if kind == "crossing":
        paths = [((-length, 0), (length, 0)), ((0, -length), (0, length))]
    elif kind == "following":
        paths = [((0, 0), (length, 0)), ((length, 0), (2 * length, 0))]
    elif kind == "parallel":
        paths = [((0, 0), (length, 0)), ((0, 20), (length, 20))]
    else:
        paths = [((-length, 0), (0, 0)), ((length, 0), (0, 0))]
    assignments = [
        UAVAssignment(
            uav_id=str(i),
            role="test",
            observation_target="line",
            camera_view="front",
            home_position=point(*p[0]),
            waypoints=[point(*p[1])],
            cruise_speed_mps=speed,
        )
        for i, p in enumerate(paths)
    ]
    return MissionPlan(
        mission_mode="benchmark",
        uav_count=2,
        safety_distance_m=5,
        assignments=assignments,
        replan_conditions=[],
    )


async def main():
    old = load("frozen_safety", ROOT / "benchmarks/baseline/uav_safety.py")
    oldexec = load("frozen_execution", ROOT / "benchmarks/baseline/uav_execution.py")
    rng = random.Random(20260919)
    rows = []
    for kind in ["crossing", "following", "parallel", "endpoint_collision"]:
        for i in range(30):
            plan = scenario(kind, rng.uniform(20, 100), rng.uniform(2, 10))
            expected = kind in ["following", "parallel"]
            rows.append(
                dict(
                    kind=kind,
                    index=i,
                    expected_safe=expected,
                    baseline_safe=old.validate_uav_mission.__wrapped__("bench", plan).approved,
                    upgraded_safe=validate_uav_mission.__wrapped__("bench", plan).approved,
                )
            )
    stats = {}
    for name in ["baseline", "upgraded"]:
        stats[name] = {
            "correct": sum(r[name + "_safe"] == r["expected_safe"] for r in rows),
            "unsafe_accepted": sum(r[name + "_safe"] and not r["expected_safe"] for r in rows),
            "safe_rejected": sum(not r[name + "_safe"] and r["expected_safe"] for r in rows),
            "total": len(rows),
        }
    with tempfile.TemporaryDirectory() as temp:
        runtime = Path(temp)
        os.environ["LINEGUARD_DATA_DIR"] = temp
        os.environ["LINEGUARD_DATABASE_URL"] = "sqlite:///" + (runtime / "tasks.db").as_posix()
        os.environ["LINEGUARD_DRYRUN_REALTIME"] = "false"
        os.environ["LINEGUARD_USE_LLM"] = "false"
        initialize_lineguard()
        counts = {}
        reused = 0
        for label, module in [("baseline", oldexec), ("upgraded", upgraded)]:
            counts[label] = 0
            original = module._execute_dryrun_vehicle

            async def counted(assignment):
                counts[label] += 1
                return await original(assignment)

            module._execute_dryrun_vehicle = counted
            for _ in range(10):
                text = "对 220kV 输电线路 3 号到 5 号塔执行强风导线和间隔棒巡检"
                task = create_task(text, [])
                plan = generate_uav_mission_plan(task.id, parse_inspection_task(task.id, text))
                if label == "upgraded":
                    upgraded.approve_mission(task.id, plan, "benchmark", UAVBackend.DRYRUN)
                for attempt in range(3):
                    result = await module.execute_uav_mission(task.id, plan, UAVBackend.DRYRUN)
                    assert result.status == "completed", result.failure_reason
                    if label == "upgraded":
                        reused += int(result.reused_result)
            module._execute_dryrun_vehicle = original
        import lineguard.database as database

        database._engine.dispose()
    output = {
        "metadata": benchmark_metadata(ROOT),
        "seed": 20260919,
        "scope": "Synthetic synchronized constant-speed dry-run trajectories; no PX4/hardware validation",
        "safety": stats,
        "cases": rows,
        "repeat_dispatch": {
            "unique_missions_per_variant": 10,
            "requests_per_mission": 3,
            "uavs_per_mission": 3,
            "vehicle_executions": counts,
            "baseline_redundant_vehicle_executions": counts["baseline"] - 30,
            "upgraded_redundant_vehicle_executions": counts["upgraded"] - 30,
            "reused_mission_results": reused,
        },
    }
    target = ROOT / "benchmarks/results/upgrade.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in output.items() if k != "cases"}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
