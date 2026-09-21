from __future__ import annotations

import math

from lineguard.audit import audited_tool
from lineguard.config import get_settings
from lineguard.models import MissionPlan, SafetyCheck, Waypoint
from lineguard.trajectory import closest_approach


def enu_to_ned(waypoint: Waypoint) -> tuple[float, float, float, float]:
    """Convert local ENU position/yaw into the PX4 local NED boundary."""
    yaw_ned = (90.0 - waypoint.yaw_deg) % 360.0
    return waypoint.y_m, waypoint.x_m, -waypoint.z_m, yaw_ned


def _distance(first: Waypoint, second: Waypoint) -> float:
    return math.sqrt(
        (first.x_m - second.x_m) ** 2
        + (first.y_m - second.y_m) ** 2
        + (first.z_m - second.z_m) ** 2
    )


@audited_tool("validate_uav_mission")
def validate_uav_mission(task_id: str, plan: MissionPlan) -> SafetyCheck:
    settings = get_settings()
    violations: list[str] = []
    limits = {
        "maximum_altitude_m": settings.maximum_altitude_m,
        "maximum_radius_m": settings.maximum_radius_m,
        "minimum_uav_separation_m": settings.minimum_uav_separation_m,
    }

    if plan.uav_count != len(plan.assignments):
        violations.append("uav_count does not match assignment count")
    identifiers = [assignment.uav_id for assignment in plan.assignments]
    if len(identifiers) != len(set(identifiers)):
        violations.append("duplicate UAV identifiers")

    if not plan.assignments:
        violations.append("mission has no UAV assignments")
    finite = all(
        math.isfinite(v)
        for assignment in plan.assignments
        for v in [
            assignment.cruise_speed_mps,
            *[
                value
                for point in [assignment.home_position, *assignment.waypoints]
                for value in [point.x_m, point.y_m, point.z_m, point.yaw_deg]
            ],
        ]
    )
    if not finite:
        return SafetyCheck(
            approved=False, violations=["non-finite mission coordinate or speed"], limits=limits
        )

    for assignment in plan.assignments:
        if not assignment.waypoints:
            violations.append(f"{assignment.uav_id} has no waypoints")
        if assignment.cruise_speed_mps <= 0 or assignment.cruise_speed_mps > 15:
            violations.append(f"{assignment.uav_id} cruise speed is outside (0, 15] m/s")
        for index, waypoint in enumerate(assignment.waypoints):
            radius = math.hypot(waypoint.x_m, waypoint.y_m)
            if waypoint.z_m < 3 or waypoint.z_m > settings.maximum_altitude_m:
                violations.append(
                    f"{assignment.uav_id} waypoint {index} altitude is outside safety limits"
                )
            if radius > settings.maximum_radius_m:
                violations.append(
                    f"{assignment.uav_id} waypoint {index} exceeds the local geofence"
                )

    for first_index, first in enumerate(plan.assignments):
        for second in plan.assignments[first_index + 1 :]:
            if first.cruise_speed_mps > 0 and second.cruise_speed_mps > 0:
                distance, when = closest_approach(first, second)
                if distance < settings.minimum_uav_separation_m - 1e-9:
                    violations.append(
                        f"{first.uav_id} and {second.uav_id} violate synchronized dryrun separation at {when:.3f}s ({distance:.3f}m)"
                    )

    return SafetyCheck(
        approved=not violations,
        violations=list(dict.fromkeys(violations)),
        limits=limits,
    )
