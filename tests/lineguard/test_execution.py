from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lineguard.execution import approve_mission, execute_uav_mission
from lineguard.models import UAVBackend, Waypoint
from lineguard.parser import parse_inspection_task
from lineguard.planner import generate_uav_mission_plan
from lineguard.repository import create_task
from lineguard.safety import enu_to_ned, validate_uav_mission


def _mission(lineguard_runtime: Path):
    text = "对 220kV 输电线路 3 号到 5 号塔执行强风导线和间隔棒巡检"
    task = create_task(text, [])
    spec = parse_inspection_task(task.id, text)
    return task, generate_uav_mission_plan(task.id, spec)


def test_mission_safety_and_enu_ned_boundary(lineguard_runtime: Path) -> None:
    task, plan = _mission(lineguard_runtime)
    check = validate_uav_mission(task.id, plan)
    assert check.approved

    north, east, down, yaw = enu_to_ned(Waypoint(x_m=10, y_m=20, z_m=30, yaw_deg=0))
    assert (north, east, down, yaw) == (20, 10, -30, 90)

    unsafe = plan.model_copy(deep=True)
    unsafe.assignments[0].waypoints[0].z_m = 100
    rejected = validate_uav_mission(task.id, unsafe)
    assert not rejected.approved
    assert any("altitude" in violation for violation in rejected.violations)


@pytest.mark.asyncio
async def test_dryrun_executes_three_uavs_and_writes_telemetry(
    lineguard_runtime: Path,
) -> None:
    task, plan = _mission(lineguard_runtime)
    approve_mission(task.id, plan, "pytest", UAVBackend.DRYRUN)
    execution = await execute_uav_mission(task.id, plan, UAVBackend.DRYRUN)

    assert execution.status == "completed"
    assert execution.backend == "dryrun"
    assert execution.safety_check.approved
    assert len(execution.vehicles) == 3
    assert all(vehicle.telemetry for vehicle in execution.vehicles)
    assert all(vehicle.completed_waypoints > 0 for vehicle in execution.vehicles)
    assert Path(execution.artifacts["telemetry_json"]).exists()
    assert Path(execution.artifacts["telemetry_csv"]).exists()


@pytest.mark.asyncio
async def test_execution_rejects_changed_approved_plan(lineguard_runtime):
    task, plan = _mission(lineguard_runtime)
    approve_mission(task.id, plan, "pytest", UAVBackend.DRYRUN)
    plan.assignments[0].cruise_speed_mps += 0.01
    result = await execute_uav_mission(task.id, plan, UAVBackend.DRYRUN)
    assert result.status == "failed"
    assert result.failure_reason == "APPROVED_CONTRACT_CHANGED"


@pytest.mark.asyncio
async def test_duplicate_execution_returns_saved_result(lineguard_runtime):
    task, plan = _mission(lineguard_runtime)
    approve_mission(task.id, plan, "pytest", UAVBackend.DRYRUN)
    first = await execute_uav_mission(task.id, plan, UAVBackend.DRYRUN)
    second = await execute_uav_mission(task.id, plan, UAVBackend.DRYRUN)
    assert first.status == second.status == "completed"
    assert second.reused_result
    assert first.vehicles == second.vehicles


@pytest.mark.asyncio
async def test_concurrent_duplicates_coalesce_to_one_execution(lineguard_runtime, monkeypatch):
    import lineguard.execution as execution_module

    task, plan = _mission(lineguard_runtime)
    approve_mission(task.id, plan, "pytest", UAVBackend.DRYRUN)
    original = execution_module._execute_uav_mission_once
    calls = 0

    async def delayed_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return await original(*args, **kwargs)

    monkeypatch.setattr(execution_module, "_execute_uav_mission_once", delayed_once)
    results = await asyncio.gather(
        *(execute_uav_mission(task.id, plan, UAVBackend.DRYRUN) for _ in range(16))
    )

    assert calls == 1
    assert all(result.status == "completed" for result in results)
    assert sum(result.reused_result for result in results) == 15
