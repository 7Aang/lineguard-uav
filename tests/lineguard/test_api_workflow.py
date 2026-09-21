from __future__ import annotations

from fastapi.testclient import TestClient

from service import app


def test_two_stage_approval_workflow(lineguard_runtime) -> None:
    client = TestClient(app)
    response = client.post(
        "/api/tasks",
        json={
            "prompt": ("对 220kV 输电线路 3 号到 5 号塔进行强风导线和间隔棒巡检，生成风险报告。"),
            "asset_ids": [],
        },
    )
    assert response.status_code == 201
    task = response.json()
    task_id = task["id"]
    assert task["status"] == "awaiting_plan_approval"
    assert task["mission_plan"]["uav_count"] == 3

    response = client.post(
        f"/api/tasks/{task_id}/reviews",
        json={
            "stage": "plan",
            "decision": "approve",
            "reviewer": "pytest",
            "reason": "plan accepted",
        },
    )
    assert response.status_code == 200
    task = response.json()
    assert task["status"] == "awaiting_report_approval"
    assert task["mission_execution"]["status"] == "completed"
    assert task["mission_execution"]["backend"] == "dryrun"
    assert len(task["mission_execution"]["vehicles"]) == 3
    assert task["risk_assessment"]["risk_level"] == "needs_review"
    assert task["report_id"]

    telemetry = client.get(f"/api/tasks/{task_id}/telemetry")
    assert telemetry.status_code == 200
    assert telemetry.json()["status"] == "completed"

    response = client.post(
        f"/api/tasks/{task_id}/reviews",
        json={
            "stage": "report",
            "decision": "approve",
            "reviewer": "pytest",
            "reason": "report accepted",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    trace = client.get(f"/api/tasks/{task_id}/trace")
    assert trace.status_code == 200
    names = [event["name"] for event in trace.json()]
    assert "task_parser" in names
    assert "generate_uav_mission_plan" in names
    assert "plan_review" in names
    assert "validate_uav_mission" in names
    assert "execute_uav_mission" in names
    assert "mission_execution" in names
    assert "generate_inspection_report" in names
    assert "report_review" in names


def test_plan_rejection_stops_workflow(lineguard_runtime) -> None:
    client = TestClient(app)
    task = client.post(
        "/api/tasks",
        json={"prompt": "巡检 220kV 3号到5号塔强风导线舞动", "asset_ids": []},
    ).json()
    response = client.post(
        f"/api/tasks/{task['id']}/reviews",
        json={
            "stage": "plan",
            "decision": "reject",
            "reviewer": "pytest",
            "reason": "wind is unsafe",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["report_id"] is None
