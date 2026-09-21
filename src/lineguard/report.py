from __future__ import annotations

import html
import json

from lineguard.audit import audited_tool
from lineguard.config import get_settings
from lineguard.models import (
    InspectionTaskSpec,
    KnowledgeSource,
    MissionExecution,
    MissionPlan,
    RiskAssessment,
    VideoAnalysis,
)
from lineguard.repository import create_report


@audited_tool("generate_inspection_report")
def generate_inspection_report(
    task_id: str,
    spec: InspectionTaskSpec,
    plan: MissionPlan,
    execution: MissionExecution,
    analysis: VideoAnalysis,
    risk: RiskAssessment,
    knowledge_sources: list[KnowledgeSource],
    narrative: str | None = None,
) -> str:
    report_dir = get_settings().artifacts_dir / task_id / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = (
        f"{spec.voltage_kv or '未知电压'}kV 线路 "
        f"{spec.tower_start or '?'}-{spec.tower_end or '?'} 号塔巡检："
        f"风险等级 {risk.risk_level.value}。"
    )
    payload = {
        "task_id": task_id,
        "summary": summary,
        "task_spec": spec.model_dump(mode="json"),
        "mission_plan": plan.model_dump(mode="json"),
        "mission_execution": execution.model_dump(mode="json"),
        "video_analysis": analysis.model_dump(mode="json"),
        "risk_assessment": risk.model_dump(mode="json"),
        "knowledge_sources": [source.model_dump(mode="json") for source in knowledge_sources],
        "narrative": narrative,
        "disclaimer": "本报告由 MVP 工具链生成，风险结论非权威结论，必须经人工审批。",
    }

    json_path = report_dir / "report.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    source_lines = [
        f"- `{source.source_id}` {source.document_name} 第 {source.page or '?'} 页："
        f"{source.excerpt[:180]}"
        for source in knowledge_sources
    ] or ["- 未检索到可引用标准，报告标记为 needs_review。"]
    markdown = "\n".join(
        [
            "# LineGuard-Agent 输电线路巡检报告",
            "",
            f"**结论：** {summary}",
            "",
            "## Agent 叙述",
            "",
            narrative or "未启用 LLM 叙述，以下内容由确定性模板生成。",
            "",
            "## 任务",
            "",
            f"- 原始指令：{spec.raw_text}",
            f"- 工况：{', '.join(spec.weather_conditions) or '未指定'}",
            f"- 目标：{', '.join(spec.targets)}",
            "",
            "## 多无人机计划",
            "",
            f"- 模式：{plan.mission_mode}",
            f"- 无人机数量：{plan.uav_count}",
            f"- 安全距离：{plan.safety_distance_m} m",
            "",
            "## 无人机仿真执行",
            "",
            f"- 执行后端：{execution.backend.value}",
            f"- 执行状态：{execution.status.value}",
            f"- 完成无人机：{len(execution.vehicles)}",
            f"- 安全校验：{execution.safety_check.approved}",
            "",
            "## 视频测量",
            "",
            f"- 状态：{analysis.status.value}",
            f"- 水平幅值：{analysis.horizontal_amplitude_px} px / "
            f"{analysis.horizontal_amplitude_m} m",
            f"- 垂直幅值：{analysis.vertical_amplitude_px} px / {analysis.vertical_amplitude_m} m",
            f"- 主频：{analysis.frequency_hz} Hz",
            f"- 椭圆倾角：{analysis.ellipse_angle_deg} deg",
            f"- 跟踪置信度：{analysis.tracking_confidence}",
            "",
            "## 风险判断",
            "",
            f"- 风险等级：{risk.risk_level.value}",
            f"- 是否权威结论：{risk.authoritative}",
            *[f"- {reason}" for reason in risk.reasons],
            "",
            "## 引用依据",
            "",
            *source_lines,
            "",
            "> 本报告由 MVP 工具链生成，所有结论必须经人工审批。",
        ]
    )
    markdown_path = report_dir / "report.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    html_path = report_dir / "report.html"
    html_path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>LineGuard Report</title>"
        "<style>body{font-family:Arial,sans-serif;max-width:960px;margin:40px auto;"
        "line-height:1.6}pre{white-space:pre-wrap}</style></head><body>"
        f"<pre>{html.escape(markdown)}</pre></body></html>",
        encoding="utf-8",
    )

    report = create_report(
        task_id=task_id,
        summary=summary,
        risk_level=risk.risk_level.value,
        json_path=str(json_path),
        markdown_path=str(markdown_path),
        html_path=str(html_path),
        payload=payload,
    )
    return report.id
