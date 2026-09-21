from __future__ import annotations

import time
from typing import Annotated, Any, Literal, TypedDict, cast

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from core import get_model
from core import settings as core_settings
from lineguard import repository
from lineguard.config import get_settings
from lineguard.execution import approve_mission, execute_uav_mission
from lineguard.models import (
    AnalysisStatus,
    InspectionTaskSpec,
    KnowledgeSource,
    MissionExecution,
    MissionExecutionStatus,
    MissionPlan,
    RiskAssessment,
    TaskStatus,
    VideoAnalysis,
)
from lineguard.parser import parse_inspection_task
from lineguard.planner import generate_uav_mission_plan
from lineguard.rag import retrieve_powerline_standards
from lineguard.report import generate_inspection_report
from lineguard.risk import calculate_galloping_risk
from lineguard.video import analyze_galloping_video


class LineGuardState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    task_id: str
    asset_ids: list[str]
    task_spec: dict[str, Any]
    knowledge_sources: list[dict[str, Any]]
    mission_plan: dict[str, Any]
    mission_execution: dict[str, Any]
    video_analysis: dict[str, Any]
    risk_assessment: dict[str, Any]
    report_id: str
    workflow_status: str


def _message_text(state: LineGuardState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _node_trace(
    task_id: str,
    name: str,
    status: str,
    output_summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    repository.add_trace(
        task_id=task_id,
        event_type="node",
        name=name,
        status=status,
        output_summary=output_summary or {},
        error=error,
    )


async def _parse_with_llm(
    task_id: str,
    text: str,
    config: RunnableConfig,
) -> InspectionTaskSpec | None:
    if not get_settings().use_llm:
        return None
    started = time.perf_counter()
    model_name = config.get("configurable", {}).get("model", core_settings.DEFAULT_MODEL)
    try:
        model = get_model(model_name)
        runnable = model.with_structured_output(InspectionTaskSpec, include_raw=True)
        result = cast(
            dict[str, Any],
            await runnable.ainvoke(
                [
                    SystemMessage(
                        content=(
                            "提取输电线路巡检任务。只提取用户明确给出的事实；"
                            "weather_conditions 和 targets 使用简短英文标识。"
                        )
                    ),
                    HumanMessage(content=text),
                ],
                config,
            ),
        )
        response = result["parsed"]
        raw_message = result.get("raw")
        usage = getattr(raw_message, "usage_metadata", None) or {}
        response.raw_text = text
        repository.add_trace(
            task_id=task_id,
            event_type="model",
            name="structured_task_parser",
            status="success",
            output_summary=response.model_dump(mode="json"),
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            model=str(model_name),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )
        return response
    except Exception as exc:
        repository.add_trace(
            task_id=task_id,
            event_type="model",
            name="structured_task_parser",
            status="fallback",
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            error=str(exc),
            model=str(model_name),
        )
        return None


async def parse_task_node(
    state: LineGuardState,
    config: RunnableConfig,
) -> LineGuardState:
    text = _message_text(state)
    task_id = state.get("task_id")
    if not task_id:
        task = repository.create_task(text, state.get("asset_ids", []))
        task_id = task.id
    repository.update_task(
        task_id,
        status=TaskStatus.PLANNING.value,
        current_stage="task_parser",
    )
    parsed = await _parse_with_llm(task_id, text, config)
    spec = parsed or parse_inspection_task(task_id, text)
    repository.update_task(task_id, task_spec=spec.model_dump(mode="json"))
    _node_trace(task_id, "task_parser", "success", spec.model_dump(mode="json"))
    return {
        "task_id": task_id,
        "task_spec": spec.model_dump(mode="json"),
        "workflow_status": TaskStatus.PLANNING.value,
    }


def retrieve_knowledge_node(state: LineGuardState) -> LineGuardState:
    task_id = state["task_id"]
    spec = InspectionTaskSpec.model_validate(state["task_spec"])
    sources = retrieve_powerline_standards(
        task_id,
        query=spec.raw_text,
        asset_ids=state.get("asset_ids", []),
    )
    serialized = [source.model_dump(mode="json") for source in sources]
    _node_trace(
        task_id,
        "knowledge_retriever",
        "success" if sources else "empty",
        {"source_count": len(sources)},
    )
    return {"knowledge_sources": serialized}


def mission_planner_node(state: LineGuardState) -> LineGuardState:
    task_id = state["task_id"]
    spec = InspectionTaskSpec.model_validate(state["task_spec"])
    plan = generate_uav_mission_plan(task_id, spec)
    serialized = plan.model_dump(mode="json")
    repository.update_task(
        task_id,
        status=TaskStatus.AWAITING_PLAN_APPROVAL.value,
        current_stage="plan_approval",
        mission_plan=serialized,
    )
    _node_trace(task_id, "mission_planner", "success", {"uav_count": plan.uav_count})
    return {
        "mission_plan": serialized,
        "workflow_status": TaskStatus.AWAITING_PLAN_APPROVAL.value,
    }


def plan_approval_node(state: LineGuardState) -> LineGuardState:
    task_id = state["task_id"]
    decision = interrupt(
        {
            "task_id": task_id,
            "stage": "plan",
            "message": "请审核多无人机观测计划。",
            "mission_plan": state["mission_plan"],
        }
    )
    approved = decision.get("decision") == "approve"
    if not approved:
        reason = decision.get("reason") or "计划被人工驳回"
        repository.update_task(
            task_id,
            status=TaskStatus.REJECTED.value,
            current_stage="plan_rejected",
            error=reason,
        )
        _node_trace(task_id, "plan_approval", "rejected", {"reason": reason})
        return {
            "workflow_status": TaskStatus.REJECTED.value,
            "messages": [AIMessage(content=f"任务已在计划审批阶段驳回：{reason}")],
        }
    approve_mission(
        task_id,
        MissionPlan.model_validate(state["mission_plan"]),
        str(decision.get("reviewer", "human-reviewer")),
    )
    repository.update_task(
        task_id,
        status=TaskStatus.EXECUTING.value,
        current_stage="mission_execution",
    )
    _node_trace(task_id, "plan_approval", "approved")
    return {"workflow_status": TaskStatus.EXECUTING.value}


def after_plan_approval(state: LineGuardState) -> Literal["execute", "end"]:
    return "end" if state.get("workflow_status") == TaskStatus.REJECTED.value else "execute"


async def mission_execution_node(state: LineGuardState) -> LineGuardState:
    task_id = state["task_id"]
    plan = MissionPlan.model_validate(state["mission_plan"])
    execution = await execute_uav_mission(task_id, plan)
    serialized = execution.model_dump(mode="json")
    if execution.status == MissionExecutionStatus.FAILED:
        repository.update_task(
            task_id,
            status=TaskStatus.FAILED.value,
            current_stage="mission_execution_failed",
            mission_execution=serialized,
            error=execution.failure_reason or "UAV mission execution failed",
        )
        _node_trace(
            task_id,
            "mission_execution",
            "failed",
            {"backend": execution.backend.value},
            execution.failure_reason,
        )
        return {
            "mission_execution": serialized,
            "workflow_status": TaskStatus.FAILED.value,
        }

    repository.update_task(
        task_id,
        status=TaskStatus.ANALYZING.value,
        current_stage="video_analysis",
        mission_execution=serialized,
    )
    _node_trace(
        task_id,
        "mission_execution",
        "success",
        {
            "backend": execution.backend.value,
            "vehicle_count": len(execution.vehicles),
        },
    )
    return {
        "mission_execution": serialized,
        "workflow_status": TaskStatus.ANALYZING.value,
    }


def after_mission_execution(state: LineGuardState) -> Literal["analyze", "end"]:
    return "end" if state.get("workflow_status") == TaskStatus.FAILED.value else "analyze"


def video_analysis_node(state: LineGuardState) -> LineGuardState:
    task_id = state["task_id"]
    assets = repository.get_assets(state.get("asset_ids", []))
    video_asset = next((asset for asset in assets if asset.asset_type == "video"), None)
    if video_asset is None:
        analysis = VideoAnalysis(
            status=AnalysisStatus.NEEDS_REVIEW,
            failure_reason="NO_VIDEO_ASSET",
        )
        _node_trace(task_id, "video_analysis", "needs_review", {"reason": "NO_VIDEO_ASSET"})
    else:
        analysis = analyze_galloping_video(task_id, video_asset.id)
        _node_trace(
            task_id,
            "video_analysis",
            analysis.status.value,
            {
                "asset_id": video_asset.id,
                "frequency_hz": analysis.frequency_hz,
                "tracking_confidence": analysis.tracking_confidence,
            },
        )
    serialized = analysis.model_dump(mode="json")
    repository.update_task(task_id, video_analysis=serialized, current_stage="risk_analysis")
    return {"video_analysis": serialized}


def risk_analysis_node(state: LineGuardState) -> LineGuardState:
    task_id = state["task_id"]
    analysis = VideoAnalysis.model_validate(state["video_analysis"])
    sources = [
        KnowledgeSource.model_validate(source) for source in state.get("knowledge_sources", [])
    ]
    risk = calculate_galloping_risk(task_id, analysis, sources)
    serialized = risk.model_dump(mode="json")
    repository.update_task(task_id, risk_assessment=serialized, current_stage="report_writer")
    _node_trace(task_id, "risk_analyzer", "success", serialized)
    return {"risk_assessment": serialized}


async def _report_narrative_with_llm(
    state: LineGuardState,
    config: RunnableConfig,
) -> str | None:
    if not get_settings().use_llm:
        return None
    task_id = state["task_id"]
    model_name = config.get("configurable", {}).get("model", core_settings.DEFAULT_MODEL)
    started = time.perf_counter()
    try:
        model = get_model(model_name)
        response = await model.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你是电力巡检报告撰写助手。只能解释输入中的结构化测量和风险结果，"
                        "不得新增数值或把非权威结论写成正式定论。输出一段简洁中文摘要。"
                    )
                ),
                HumanMessage(
                    content=str(
                        {
                            "task_spec": state["task_spec"],
                            "video_analysis": state["video_analysis"],
                            "risk_assessment": state["risk_assessment"],
                        }
                    )
                ),
            ],
            config,
        )
        usage = getattr(response, "usage_metadata", None) or {}
        narrative = str(response.content)
        repository.add_trace(
            task_id=task_id,
            event_type="model",
            name="report_narrative",
            status="success",
            output_summary={"narrative": narrative},
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            model=str(model_name),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )
        return narrative
    except Exception as exc:
        repository.add_trace(
            task_id=task_id,
            event_type="model",
            name="report_narrative",
            status="fallback",
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            error=str(exc),
            model=str(model_name),
        )
        return None


async def report_writer_node(
    state: LineGuardState,
    config: RunnableConfig,
) -> LineGuardState:
    task_id = state["task_id"]
    narrative = await _report_narrative_with_llm(state, config)
    report_id = generate_inspection_report(
        task_id=task_id,
        spec=InspectionTaskSpec.model_validate(state["task_spec"]),
        plan=MissionPlan.model_validate(state["mission_plan"]),
        execution=MissionExecution.model_validate(state["mission_execution"]),
        analysis=VideoAnalysis.model_validate(state["video_analysis"]),
        risk=RiskAssessment.model_validate(state["risk_assessment"]),
        knowledge_sources=[
            KnowledgeSource.model_validate(source) for source in state.get("knowledge_sources", [])
        ],
        narrative=narrative,
    )
    repository.update_task(
        task_id,
        status=TaskStatus.AWAITING_REPORT_APPROVAL.value,
        current_stage="report_approval",
        report_id=report_id,
    )
    _node_trace(task_id, "report_writer", "success", {"report_id": report_id})
    return {
        "report_id": report_id,
        "workflow_status": TaskStatus.AWAITING_REPORT_APPROVAL.value,
    }


def report_approval_node(state: LineGuardState) -> LineGuardState:
    task_id = state["task_id"]
    decision = interrupt(
        {
            "task_id": task_id,
            "stage": "report",
            "message": "请审核巡检报告及其证据链。",
            "report_id": state["report_id"],
        }
    )
    approved = decision.get("decision") == "approve"
    if not approved:
        reason = decision.get("reason") or "报告被人工驳回"
        repository.update_task(
            task_id,
            status=TaskStatus.REJECTED.value,
            current_stage="report_rejected",
            error=reason,
        )
        _node_trace(task_id, "report_approval", "rejected", {"reason": reason})
        return {
            "workflow_status": TaskStatus.REJECTED.value,
            "messages": [AIMessage(content=f"任务已在报告审批阶段驳回：{reason}")],
        }
    repository.update_task(
        task_id,
        status=TaskStatus.COMPLETED.value,
        current_stage="completed",
    )
    _node_trace(task_id, "report_approval", "approved")
    return {
        "workflow_status": TaskStatus.COMPLETED.value,
        "messages": [
            AIMessage(
                content=(
                    f"LineGuard-Agent 任务已完成，报告 ID：{state['report_id']}。"
                    "报告结论仍需按企业流程归档。"
                )
            )
        ],
    }


builder = StateGraph(LineGuardState)
builder.add_node("task_parser", parse_task_node)
builder.add_node("knowledge_retriever", retrieve_knowledge_node)
builder.add_node("mission_planner", mission_planner_node)
builder.add_node("plan_approval", plan_approval_node)
builder.add_node("mission_execution", mission_execution_node)
builder.add_node("video_analysis", video_analysis_node)
builder.add_node("risk_analyzer", risk_analysis_node)
builder.add_node("report_writer", report_writer_node)
builder.add_node("report_approval", report_approval_node)
builder.set_entry_point("task_parser")
builder.add_edge("task_parser", "knowledge_retriever")
builder.add_edge("knowledge_retriever", "mission_planner")
builder.add_edge("mission_planner", "plan_approval")
builder.add_conditional_edges(
    "plan_approval",
    after_plan_approval,
    {"execute": "mission_execution", "end": END},
)
builder.add_conditional_edges(
    "mission_execution",
    after_mission_execution,
    {"analyze": "video_analysis", "end": END},
)
builder.add_edge("video_analysis", "risk_analyzer")
builder.add_edge("risk_analyzer", "report_writer")
builder.add_edge("report_writer", "report_approval")
builder.add_edge("report_approval", END)

lineguard_agent = builder.compile(checkpointer=InMemorySaver())
lineguard_agent.name = "lineguard-agent"
