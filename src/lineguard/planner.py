from __future__ import annotations

from lineguard.audit import audited_tool
from lineguard.models import InspectionTaskSpec, MissionPlan, UAVAssignment, Waypoint


def _waypoints(lateral_m: float, altitude_m: float, yaw_deg: float) -> list[Waypoint]:
    return [
        Waypoint(x_m=0.0, y_m=lateral_m, z_m=altitude_m, yaw_deg=yaw_deg),
        Waypoint(x_m=50.0, y_m=lateral_m, z_m=altitude_m, yaw_deg=yaw_deg),
        Waypoint(x_m=100.0, y_m=lateral_m, z_m=altitude_m, yaw_deg=yaw_deg),
    ]


@audited_tool("generate_uav_mission_plan")
def generate_uav_mission_plan(task_id: str, spec: InspectionTaskSpec) -> MissionPlan:
    strong_wind = "strong_wind" in spec.weather_conditions
    safety_distance = 8.0 if strong_wind else 5.0

    assignments = [
        UAVAssignment(
            uav_id="uav-1",
            role="left_side_observer",
            observation_target="conductor_galloping",
            camera_view="low_angle_side_view",
            home_position=Waypoint(x_m=0, y_m=-12, z_m=0, yaw_deg=90),
            waypoints=_waypoints(-safety_distance, 20.0, 90.0),
        ),
        UAVAssignment(
            uav_id="uav-2",
            role="top_observer",
            observation_target="span_motion_envelope",
            camera_view="top_down_view",
            home_position=Waypoint(x_m=0, y_m=0, z_m=0, yaw_deg=0),
            waypoints=_waypoints(0.0, 35.0, 0.0),
        ),
        UAVAssignment(
            uav_id="uav-3",
            role="spacer_reinspection",
            observation_target="spacer" if "spacer" in spec.targets else "conductor_detail",
            camera_view="local_close_view",
            home_position=Waypoint(x_m=0, y_m=12, z_m=0, yaw_deg=-90),
            waypoints=[
                Waypoint(x_m=45.0, y_m=safety_distance, z_m=22.0, yaw_deg=-90.0),
                Waypoint(x_m=55.0, y_m=safety_distance, z_m=22.0, yaw_deg=-90.0),
            ],
        ),
    ]

    return MissionPlan(
        mission_mode="multi_view_galloping_observation",
        uav_count=3,
        safety_distance_m=safety_distance,
        assignments=assignments,
        replan_conditions=[
            "wind_speed_mps > 10",
            "obstacle_detected",
            "tracking_confidence < 0.60",
            "communication_link_lost",
        ],
    )
