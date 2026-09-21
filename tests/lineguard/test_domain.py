from __future__ import annotations

from lineguard.parser import parse_inspection_task
from lineguard.planner import generate_uav_mission_plan
from lineguard.repository import create_task


def test_chinese_task_parsing_and_three_uav_plan(lineguard_runtime) -> None:
    text = (
        "对 220kV 某输电线路 3 号到 5 号塔之间进行强风工况下的导地线舞动巡检，"
        "重点观察间隔棒和导线舞动幅值，任务完成后生成风险报告。"
    )
    task = create_task(text, [])
    spec = parse_inspection_task(task.id, text)

    assert spec.voltage_kv == 220
    assert spec.tower_start == 3
    assert spec.tower_end == 5
    assert "strong_wind" in spec.weather_conditions
    assert {"conductor", "spacer"}.issubset(spec.targets)

    plan = generate_uav_mission_plan(task.id, spec)
    assert plan.uav_count == 3
    assert plan.safety_distance_m == 8.0
    assert {assignment.role for assignment in plan.assignments} == {
        "left_side_observer",
        "top_observer",
        "spacer_reinspection",
    }
