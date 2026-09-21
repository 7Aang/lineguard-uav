from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    EXECUTING = "executing"
    ANALYZING = "analyzing"
    AWAITING_REPORT_APPROVAL = "awaiting_report_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class AssetType(StrEnum):
    PDF = "pdf"
    VIDEO = "video"


class AnalysisStatus(StrEnum):
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    NEEDS_REVIEW = "needs_review"


class UAVBackend(StrEnum):
    DRYRUN = "dryrun"
    MAVSDK = "mavsdk"


class MissionExecutionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class InspectionTaskSpec(BaseModel):
    raw_text: str
    voltage_kv: int | None = None
    tower_start: int | None = None
    tower_end: int | None = None
    weather_conditions: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)
    requested_outputs: list[str] = Field(default_factory=lambda: ["risk_report"])
    mission_objective: str = "transmission_line_galloping_inspection"


class Waypoint(BaseModel):
    x_m: float
    y_m: float
    z_m: float
    yaw_deg: float


class UAVAssignment(BaseModel):
    uav_id: str
    role: str
    observation_target: str
    camera_view: str
    home_position: Waypoint
    cruise_speed_mps: float = 5.0
    waypoints: list[Waypoint]


class MissionPlan(BaseModel):
    mission_mode: str
    uav_count: int
    safety_distance_m: float
    assignments: list[UAVAssignment]
    replan_conditions: list[str]
    coordinate_frame: Literal["local_enu"] = "local_enu"
    deterministic: bool = True


class SafetyCheck(BaseModel):
    approved: bool
    violations: list[str] = Field(default_factory=list)
    limits: dict[str, float] = Field(default_factory=dict)


class TelemetrySample(BaseModel):
    uav_id: str
    sequence: int
    elapsed_s: float
    x_m: float
    y_m: float
    z_m: float
    yaw_deg: float
    battery_percent: float
    flight_mode: str
    target_waypoint: int | None = None


class UAVExecutionResult(BaseModel):
    uav_id: str
    status: MissionExecutionStatus
    completed_waypoints: int
    final_position: Waypoint | None = None
    telemetry: list[TelemetrySample] = Field(default_factory=list)
    failure_reason: str | None = None


class MissionExecution(BaseModel):
    backend: UAVBackend
    status: MissionExecutionStatus
    safety_check: SafetyCheck
    vehicles: list[UAVExecutionResult] = Field(default_factory=list)
    duration_ms: float = 0.0
    artifacts: dict[str, str] = Field(default_factory=dict)
    failure_reason: str | None = None
    reused_result: bool = False
    execution_contract: str | None = None


class KnowledgeSource(BaseModel):
    source_id: str
    document_name: str
    page: int | None = None
    section: str | None = None
    excerpt: str
    score: float


class VideoArtifacts(BaseModel):
    evidence_frame: str | None = None
    displacement_csv: str | None = None
    displacement_plot: str | None = None


class VideoAnalysis(BaseModel):
    status: AnalysisStatus
    asset_id: str | None = None
    frame_count: int = 0
    valid_frame_count: int = 0
    fps: float | None = None
    horizontal_amplitude_px: float | None = None
    vertical_amplitude_px: float | None = None
    horizontal_amplitude_m: float | None = None
    vertical_amplitude_m: float | None = None
    frequency_hz: float | None = None
    ellipse_angle_deg: float | None = None
    tracking_confidence: float = 0.0
    failure_reason: str | None = None
    artifacts: VideoArtifacts = Field(default_factory=VideoArtifacts)


class RiskAssessment(BaseModel):
    risk_level: RiskLevel
    authoritative: bool = False
    reasons: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    thresholds: dict[str, float] = Field(default_factory=dict)


class ReviewDecision(BaseModel):
    stage: Literal["plan", "report"]
    decision: Literal["approve", "reject"]
    reviewer: str = "human-reviewer"
    reason: str | None = None


class TraceEvent(BaseModel):
    id: str
    task_id: str
    sequence: int
    event_type: str
    name: str
    status: str
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float | None = None
    error: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: datetime


class TaskCreate(BaseModel):
    prompt: str = Field(min_length=1)
    asset_ids: list[str] = Field(default_factory=list)


class TaskView(BaseModel):
    id: str
    prompt: str
    asset_ids: list[str]
    status: TaskStatus
    current_stage: str | None = None
    task_spec: InspectionTaskSpec | None = None
    mission_plan: MissionPlan | None = None
    mission_execution: MissionExecution | None = None
    video_analysis: VideoAnalysis | None = None
    risk_assessment: RiskAssessment | None = None
    report_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class AssetView(BaseModel):
    id: str
    filename: str
    asset_type: AssetType
    content_type: str | None = None
    size_bytes: int
    status: str
    calibration: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class VideoAnalyzeRequest(BaseModel):
    asset_id: str
    task_id: str | None = None


class ReportView(BaseModel):
    id: str
    task_id: str
    summary: str
    risk_level: RiskLevel
    json_path: str
    markdown_path: str
    html_path: str
    artifact_urls: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any]
    created_at: datetime
