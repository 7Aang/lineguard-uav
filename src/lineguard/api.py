from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from lineguard import repository
from lineguard.config import get_settings
from lineguard.lifecycle import TERMINAL_STATUSES, InvalidTaskTransition
from lineguard.models import (
    AssetType,
    AssetView,
    InspectionTaskSpec,
    MissionExecution,
    MissionPlan,
    ReportView,
    ReviewDecision,
    RiskAssessment,
    TaskCreate,
    TaskStatus,
    TaskView,
    TraceEvent,
    VideoAnalysis,
    VideoAnalyzeRequest,
)
from lineguard.rag import index_pdf_asset
from lineguard.video import analyze_galloping_video
from lineguard.workflow import LineGuardState, lineguard_agent

router = APIRouter(prefix="/api", tags=["lineguard"])


def _mark_failed(task_id: str, stage: str, error: str) -> None:
    record = repository.get_task(task_id)
    if record is None or TaskStatus(record.status) in TERMINAL_STATUSES:
        return
    repository.transition_task(
        task_id,
        TaskStatus.FAILED,
        expected=TaskStatus(record.status),
        current_stage=stage,
        error=error,
    )


class ReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contract: str = Field(min_length=64, max_length=64)
    reviewer: str = Field(min_length=1, max_length=100)
    resolution: Literal["not_executed", "aborted", "completed"]
    evidence: str = Field(min_length=20, max_length=4000)
    executor_stopped: bool


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reviewer: str = Field(min_length=1, max_length=100)


@router.get("/tasks/{task_id}/execution-ledger")
async def execution_ledger(task_id: str):
    from lineguard.recovery import ledger

    row = ledger().inspect(task_id)
    if row is None:
        raise HTTPException(404, "Execution ledger not found")
    return {"execution": row, "events": ledger().history(task_id)}


@router.post("/tasks/{task_id}/reconcile")
async def reconcile_execution(task_id: str, payload: ReconcileRequest):
    from lineguard.recovery import ledger
    from lineguard.reliability import DispatchDenied

    try:
        return ledger().reconcile(task_id, **payload.model_dump())
    except DispatchDenied as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/tasks/{task_id}/recovery", response_model=TaskView)
async def recovery_draft(task_id: str, payload: RecoveryRequest):
    from lineguard.recovery import create_recovery_draft
    from lineguard.reliability import DispatchDenied

    try:
        return _task_view(create_recovery_draft(task_id, payload.reviewer))
    except KeyError as exc:
        raise HTTPException(404, "Source task not found") from exc
    except DispatchDenied as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/tasks/{task_id}/start", response_model=TaskView)
async def start_recovery_draft(task_id: str):
    task = repository.get_task(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    try:
        repository.transition_task(
            task_id,
            TaskStatus.PLANNING,
            expected=TaskStatus.CREATED,
            expected_stage="recovery_draft",
            current_stage="recovery_planning",
        )
    except InvalidTaskTransition:
        return _task_view(task)
    try:
        await lineguard_agent.ainvoke(
            cast(
                Any,
                {
                    "messages": [HumanMessage(content=task.prompt)],
                    "task_id": task.id,
                    "asset_ids": task.asset_ids,
                },
            ),
            config=_graph_config(task.id),
        )
    except Exception as exc:
        _mark_failed(task.id, "recovery_planning_failed", type(exc).__name__)
        raise HTTPException(500, "Recovery planning failed") from exc
    return _task_view(repository.get_task(task_id))


def _asset_view(record) -> AssetView:
    return AssetView(
        id=record.id,
        filename=record.filename,
        asset_type=record.asset_type,
        content_type=record.content_type,
        size_bytes=record.size_bytes,
        status=record.status,
        calibration=record.calibration or {},
        created_at=record.created_at,
    )


def _task_view(record) -> TaskView:
    return TaskView(
        id=record.id,
        prompt=record.prompt,
        asset_ids=record.asset_ids or [],
        status=TaskStatus(record.status),
        current_stage=record.current_stage,
        task_spec=(
            InspectionTaskSpec.model_validate(record.task_spec) if record.task_spec else None
        ),
        mission_plan=(
            MissionPlan.model_validate(record.mission_plan) if record.mission_plan else None
        ),
        mission_execution=(
            MissionExecution.model_validate(record.mission_execution)
            if record.mission_execution
            else None
        ),
        video_analysis=(
            VideoAnalysis.model_validate(record.video_analysis) if record.video_analysis else None
        ),
        risk_assessment=(
            RiskAssessment.model_validate(record.risk_assessment)
            if record.risk_assessment
            else None
        ),
        report_id=record.report_id,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _report_view(record) -> ReportView:
    artifact_urls = {
        "json": f"/lineguard-artifacts/{record.task_id}/report/report.json",
        "markdown": f"/lineguard-artifacts/{record.task_id}/report/report.md",
        "html": f"/lineguard-artifacts/{record.task_id}/report/report.html",
    }
    analysis_artifacts = record.payload.get("video_analysis", {}).get("artifacts", {})
    execution_artifacts = record.payload.get("mission_execution", {}).get("artifacts", {})
    for name, path in {**analysis_artifacts, **execution_artifacts}.items():
        if not path:
            continue
        parts = Path(path).parts
        if "artifacts" in parts:
            index = parts.index("artifacts")
            artifact_urls[name] = f"/lineguard-artifacts/{'/'.join(parts[index + 1 :])}"
    return ReportView(
        id=record.id,
        task_id=record.task_id,
        summary=record.summary,
        risk_level=record.risk_level,
        json_path=record.json_path,
        markdown_path=record.markdown_path,
        html_path=record.html_path,
        artifact_urls=artifact_urls,
        payload=record.payload,
        created_at=record.created_at,
    )


def _graph_config(task_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": task_id, "user_id": "lineguard-api"}}


@router.post("/assets", response_model=AssetView, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    file: Annotated[UploadFile, File()],
    asset_type: Annotated[AssetType, Form()],
    pixels_per_meter: Annotated[float | None, Form()] = None,
    roi_json: Annotated[str | None, Form()] = None,
) -> AssetView:
    safe_name = Path(file.filename or "asset").name
    suffix = Path(safe_name).suffix.lower()
    if asset_type == AssetType.PDF and suffix != ".pdf":
        raise HTTPException(status_code=422, detail="PDF asset must use .pdf extension")
    if asset_type == AssetType.VIDEO and suffix not in {".mp4", ".avi", ".mov", ".mkv"}:
        raise HTTPException(status_code=422, detail="Unsupported video extension")

    calibration: dict = {}
    if pixels_per_meter is not None:
        if pixels_per_meter <= 0:
            raise HTTPException(status_code=422, detail="pixels_per_meter must be positive")
        calibration["pixels_per_meter"] = pixels_per_meter
    if roi_json:
        try:
            roi = json.loads(roi_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="roi_json must be valid JSON") from exc
        if not isinstance(roi, list) or len(roi) != 4:
            raise HTTPException(status_code=422, detail="roi_json must be [x, y, width, height]")
        if not all(isinstance(value, (int, float)) for value in roi):
            raise HTTPException(status_code=422, detail="ROI values must be numeric")
        if roi[2] <= 0 or roi[3] <= 0:
            raise HTTPException(status_code=422, detail="ROI width and height must be positive")
        calibration["roi"] = roi

    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded asset is empty")
    storage_name = f"{uuid4()}{suffix}"
    storage_path = get_settings().assets_dir / storage_name
    storage_path.write_bytes(content)
    record = repository.create_asset(
        filename=safe_name,
        asset_type=asset_type.value,
        storage_path=str(storage_path),
        content_type=file.content_type,
        size_bytes=len(content),
        status="stored",
        calibration=calibration,
    )
    if asset_type == AssetType.PDF:
        try:
            index_pdf_asset(record.id)
        except Exception:
            repository.update_asset(record.id, status="index_failed")
    return _asset_view(repository.get_asset(record.id))


@router.post("/tasks", response_model=TaskView, status_code=status.HTTP_201_CREATED)
async def submit_task(payload: TaskCreate) -> TaskView:
    existing_assets = repository.get_assets(payload.asset_ids)
    if len(existing_assets) != len(set(payload.asset_ids)):
        raise HTTPException(status_code=422, detail="One or more asset IDs do not exist")
    task = repository.create_task(payload.prompt, payload.asset_ids)
    repository.add_trace(
        task_id=task.id,
        event_type="api",
        name="submit_task",
        status="accepted",
        input_summary=payload.model_dump(mode="json"),
    )
    try:
        graph_input: LineGuardState = {
            "messages": [HumanMessage(content=payload.prompt)],
            "task_id": task.id,
            "asset_ids": payload.asset_ids,
        }
        await lineguard_agent.ainvoke(
            cast(Any, graph_input),
            config=_graph_config(task.id),
        )
    except Exception as exc:
        _mark_failed(task.id, "failed", str(exc))
        repository.add_trace(
            task_id=task.id,
            event_type="workflow",
            name="initial_run",
            status="failed",
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Task execution failed") from exc
    return _task_view(repository.get_task(task.id))


@router.get("/tasks/{task_id}", response_model=TaskView)
async def get_task(task_id: str) -> TaskView:
    task = repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_view(task)


@router.post("/tasks/{task_id}/reviews", response_model=TaskView)
async def review_task(task_id: str, review: ReviewDecision) -> TaskView:
    task = repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        repository.claim_review(task_id, review.stage, **review.model_dump(exclude={"stage"}))
    except InvalidTaskTransition as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    repository.add_trace(
        task_id=task_id,
        event_type="review",
        name=f"{review.stage}_review",
        status=review.decision,
        input_summary=review.model_dump(mode="json"),
    )
    try:
        await lineguard_agent.ainvoke(
            Command(resume=review.model_dump(mode="json")),
            config=_graph_config(task_id),
        )
    except Exception as exc:
        _mark_failed(task_id, "failed", str(exc))
        repository.add_trace(
            task_id=task_id,
            event_type="workflow",
            name=f"resume_{review.stage}",
            status="failed",
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Workflow resume failed") from exc
    return _task_view(repository.get_task(task_id))


@router.get("/metrics")
async def get_operational_metrics() -> dict[str, Any]:
    """Expose database-backed counters for local operations and smoke tests."""

    return repository.operational_metrics()


@router.get("/tasks/{task_id}/trace", response_model=list[TraceEvent])
async def get_trace(task_id: str) -> list[TraceEvent]:
    if repository.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return [
        TraceEvent(
            id=record.id,
            task_id=record.task_id,
            sequence=record.sequence,
            event_type=record.event_type,
            name=record.name,
            status=record.status,
            input_summary=record.input_summary or {},
            output_summary=record.output_summary or {},
            duration_ms=record.duration_ms,
            error=record.error,
            model=record.model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            created_at=record.created_at,
        )
        for record in repository.list_trace(task_id)
    ]


@router.get("/tasks/{task_id}/telemetry", response_model=MissionExecution)
async def get_telemetry(task_id: str) -> MissionExecution:
    task = repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.mission_execution:
        raise HTTPException(status_code=409, detail="Task has no mission execution")
    return MissionExecution.model_validate(task.mission_execution)


@router.post("/videos/analyze", response_model=VideoAnalysis)
async def analyze_video(payload: VideoAnalyzeRequest) -> VideoAnalysis:
    asset = repository.get_asset(payload.asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if asset.asset_type != AssetType.VIDEO.value:
        raise HTTPException(status_code=422, detail="Asset is not a video")
    if payload.task_id and repository.get_task(payload.task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return analyze_galloping_video(payload.task_id, payload.asset_id)


@router.get("/reports/{report_id}", response_model=ReportView)
async def get_report(report_id: str) -> ReportView:
    report = repository.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return _report_view(report)
