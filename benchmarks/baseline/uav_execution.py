from __future__ import annotations

import asyncio
import csv
import json
import math
import time
from typing import Any

from lineguard.audit import audited_tool
from lineguard.config import get_settings
from lineguard.models import (
    MissionExecution,
    MissionExecutionStatus,
    MissionPlan,
    SafetyCheck,
    TelemetrySample,
    UAVAssignment,
    UAVBackend,
    UAVExecutionResult,
    Waypoint,
)
from lineguard.safety import enu_to_ned, validate_uav_mission


def _interpolate(first: Waypoint, second: Waypoint, ratio: float) -> Waypoint:
    return Waypoint(
        x_m=first.x_m + (second.x_m - first.x_m) * ratio,
        y_m=first.y_m + (second.y_m - first.y_m) * ratio,
        z_m=first.z_m + (second.z_m - first.z_m) * ratio,
        yaw_deg=first.yaw_deg + (second.yaw_deg - first.yaw_deg) * ratio,
    )


def _write_telemetry_artifacts(
    task_id: str,
    backend: UAVBackend,
    vehicles: list[UAVExecutionResult],
) -> dict[str, str]:
    artifact_dir = get_settings().artifacts_dir / task_id / "simulation"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifact_dir / "telemetry.json"
    csv_path = artifact_dir / "telemetry.csv"
    payload = {
        "backend": backend.value,
        "vehicles": [vehicle.model_dump(mode="json") for vehicle in vehicles],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "uav_id",
                "sequence",
                "elapsed_s",
                "x_m",
                "y_m",
                "z_m",
                "yaw_deg",
                "battery_percent",
                "flight_mode",
                "target_waypoint",
            ]
        )
        for vehicle in vehicles:
            for sample in vehicle.telemetry:
                writer.writerow(
                    [
                        sample.uav_id,
                        sample.sequence,
                        sample.elapsed_s,
                        sample.x_m,
                        sample.y_m,
                        sample.z_m,
                        sample.yaw_deg,
                        sample.battery_percent,
                        sample.flight_mode,
                        sample.target_waypoint,
                    ]
                )
    return {"telemetry_json": str(json_path), "telemetry_csv": str(csv_path)}


async def _execute_dryrun_vehicle(assignment: UAVAssignment) -> UAVExecutionResult:
    settings = get_settings()
    telemetry: list[TelemetrySample] = []
    current = assignment.home_position
    sequence = 0
    elapsed = 0.0
    battery = 100.0

    for waypoint_index, target in enumerate(assignment.waypoints):
        distance = math.sqrt(
            (target.x_m - current.x_m) ** 2
            + (target.y_m - current.y_m) ** 2
            + (target.z_m - current.z_m) ** 2
        )
        duration = max(1.0, distance / assignment.cruise_speed_mps)
        steps = max(2, int(math.ceil(duration)))
        for step in range(1, steps + 1):
            point = _interpolate(current, target, step / steps)
            elapsed += duration / steps
            sequence += 1
            battery = max(20.0, battery - 0.04)
            telemetry.append(
                TelemetrySample(
                    uav_id=assignment.uav_id,
                    sequence=sequence,
                    elapsed_s=round(elapsed, 3),
                    x_m=round(point.x_m, 3),
                    y_m=round(point.y_m, 3),
                    z_m=round(point.z_m, 3),
                    yaw_deg=round(point.yaw_deg, 3),
                    battery_percent=round(battery, 3),
                    flight_mode="OFFBOARD_DRYRUN",
                    target_waypoint=waypoint_index,
                )
            )
            if settings.dryrun_realtime:
                await asyncio.sleep(duration / steps)
        current = target

    return UAVExecutionResult(
        uav_id=assignment.uav_id,
        status=MissionExecutionStatus.COMPLETED,
        completed_waypoints=len(assignment.waypoints),
        final_position=current,
        telemetry=telemetry,
    )


async def _wait_for_connection(drone: Any, timeout_s: float = 30.0) -> None:
    async def wait() -> None:
        async for state in drone.core.connection_state():
            if state.is_connected:
                return

    await asyncio.wait_for(wait(), timeout=timeout_s)


async def _wait_for_local_position(drone: Any, timeout_s: float = 30.0) -> None:
    async def wait() -> None:
        async for health in drone.telemetry.health():
            if health.is_local_position_ok:
                return

    await asyncio.wait_for(wait(), timeout=timeout_s)


async def _wait_for_waypoint(
    drone: Any,
    assignment: UAVAssignment,
    waypoint: Waypoint,
    waypoint_index: int,
    telemetry: list[TelemetrySample],
    started: float,
) -> None:
    settings = get_settings()

    async def wait() -> None:
        sequence = len(telemetry)
        async for position_velocity in drone.telemetry.position_velocity_ned():
            position = position_velocity.position
            x_m = float(position.east_m) + assignment.home_position.x_m
            y_m = float(position.north_m) + assignment.home_position.y_m
            z_m = float(-position.down_m) + assignment.home_position.z_m
            sequence += 1
            telemetry.append(
                TelemetrySample(
                    uav_id=assignment.uav_id,
                    sequence=sequence,
                    elapsed_s=round(time.perf_counter() - started, 3),
                    x_m=round(x_m, 3),
                    y_m=round(y_m, 3),
                    z_m=round(z_m, 3),
                    yaw_deg=waypoint.yaw_deg,
                    battery_percent=max(0.0, 100.0 - sequence * 0.02),
                    flight_mode="OFFBOARD",
                    target_waypoint=waypoint_index,
                )
            )
            distance = math.sqrt(
                (x_m - waypoint.x_m) ** 2 + (y_m - waypoint.y_m) ** 2 + (z_m - waypoint.z_m) ** 2
            )
            if distance <= settings.waypoint_tolerance_m:
                return

    await asyncio.wait_for(wait(), timeout=settings.waypoint_timeout_s)


async def _execute_mavsdk_vehicle(
    assignment: UAVAssignment,
    connection: str,
    server_port: int,
) -> UAVExecutionResult:
    try:
        from mavsdk import System  # type: ignore[import-untyped]
        from mavsdk.offboard import (  # type: ignore[import-untyped]
            OffboardError,
            PositionNedYaw,
        )
    except ImportError:
        return UAVExecutionResult(
            uav_id=assignment.uav_id,
            status=MissionExecutionStatus.FAILED,
            completed_waypoints=0,
            failure_reason=("MAVSDK_NOT_INSTALLED: run `uv sync --group uav` before PX4 execution"),
        )

    drone = System(port=server_port)
    telemetry: list[TelemetrySample] = []
    completed_waypoints = 0
    started = time.perf_counter()
    try:
        await drone.connect(system_address=connection)
        await _wait_for_connection(drone)
        await _wait_for_local_position(drone)
        first = _relative_waypoint(assignment, assignment.waypoints[0])
        north, east, down, yaw = enu_to_ned(first)
        await drone.action.arm()
        await drone.offboard.set_position_ned(PositionNedYaw(north, east, down, yaw))
        await drone.offboard.start()

        for waypoint_index, waypoint in enumerate(assignment.waypoints):
            local_waypoint = _relative_waypoint(assignment, waypoint)
            north, east, down, yaw = enu_to_ned(local_waypoint)
            await drone.offboard.set_position_ned(PositionNedYaw(north, east, down, yaw))
            await _wait_for_waypoint(
                drone,
                assignment,
                waypoint,
                waypoint_index,
                telemetry,
                started,
            )
            completed_waypoints += 1

        try:
            await drone.offboard.stop()
        except OffboardError:
            pass
        await drone.action.land()
        return UAVExecutionResult(
            uav_id=assignment.uav_id,
            status=MissionExecutionStatus.COMPLETED,
            completed_waypoints=completed_waypoints,
            final_position=assignment.waypoints[-1],
            telemetry=telemetry,
        )
    except Exception as exc:
        try:
            await drone.action.land()
        except Exception:
            pass
        return UAVExecutionResult(
            uav_id=assignment.uav_id,
            status=MissionExecutionStatus.FAILED,
            completed_waypoints=completed_waypoints,
            telemetry=telemetry,
            failure_reason=f"{type(exc).__name__}: {exc}",
        )


def _relative_waypoint(
    assignment: UAVAssignment,
    waypoint: Waypoint,
) -> Waypoint:
    return Waypoint(
        x_m=waypoint.x_m - assignment.home_position.x_m,
        y_m=waypoint.y_m - assignment.home_position.y_m,
        z_m=waypoint.z_m - assignment.home_position.z_m,
        yaw_deg=waypoint.yaw_deg,
    )


def _failed_execution(
    backend: UAVBackend,
    safety_check: SafetyCheck,
    reason: str,
    duration_ms: float,
) -> MissionExecution:
    return MissionExecution(
        backend=backend,
        status=MissionExecutionStatus.FAILED,
        safety_check=safety_check,
        duration_ms=duration_ms,
        failure_reason=reason,
    )


@audited_tool("execute_uav_mission")
async def execute_uav_mission(
    task_id: str,
    plan: MissionPlan,
    backend: UAVBackend | None = None,
) -> MissionExecution:
    started = time.perf_counter()
    settings = get_settings()
    selected_backend = backend or settings.uav_backend
    safety_check = validate_uav_mission(task_id, plan)
    if not safety_check.approved:
        return _failed_execution(
            selected_backend,
            safety_check,
            "MISSION_SAFETY_REJECTED",
            round((time.perf_counter() - started) * 1000, 3),
        )

    if selected_backend == UAVBackend.DRYRUN:
        vehicles = list(
            await asyncio.gather(
                *[_execute_dryrun_vehicle(assignment) for assignment in plan.assignments]
            )
        )
    else:
        if len(settings.px4_connections) < len(plan.assignments):
            return _failed_execution(
                selected_backend,
                safety_check,
                "INSUFFICIENT_PX4_CONNECTIONS",
                round((time.perf_counter() - started) * 1000, 3),
            )
        vehicles = list(
            await asyncio.gather(
                *[
                    _execute_mavsdk_vehicle(
                        assignment,
                        settings.px4_connections[index],
                        50051 + index,
                    )
                    for index, assignment in enumerate(plan.assignments)
                ]
            )
        )

    failed = [vehicle for vehicle in vehicles if vehicle.status == "failed"]
    status = MissionExecutionStatus.FAILED if failed else MissionExecutionStatus.COMPLETED
    artifacts = _write_telemetry_artifacts(task_id, selected_backend, vehicles)
    return MissionExecution(
        backend=selected_backend,
        status=status,
        safety_check=safety_check,
        vehicles=vehicles,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
        artifacts=artifacts,
        failure_reason=(
            "; ".join(f"{vehicle.uav_id}: {vehicle.failure_reason}" for vehicle in failed)
            if failed
            else None
        ),
    )
