from __future__ import annotations

from lineguard.audit import audited_tool
from lineguard.config import get_settings
from lineguard.models import (
    AnalysisStatus,
    KnowledgeSource,
    RiskAssessment,
    RiskLevel,
    VideoAnalysis,
)


@audited_tool("calculate_galloping_risk")
def calculate_galloping_risk(
    task_id: str,
    analysis: VideoAnalysis,
    knowledge_sources: list[KnowledgeSource],
) -> RiskAssessment:
    settings = get_settings()
    thresholds = {
        "medium_amplitude_m": settings.medium_amplitude_m,
        "high_amplitude_m": settings.high_amplitude_m,
        "minimum_tracking_confidence": settings.minimum_tracking_confidence,
    }
    evidence = [f"video_analysis:{analysis.asset_id}"]
    evidence.extend(f"knowledge:{source.source_id}" for source in knowledge_sources)

    if analysis.status != AnalysisStatus.COMPLETED:
        return RiskAssessment(
            risk_level=RiskLevel.NEEDS_REVIEW,
            reasons=[analysis.failure_reason or "视频分析未完成"],
            evidence=evidence,
            thresholds=thresholds,
        )
    if analysis.tracking_confidence < settings.minimum_tracking_confidence:
        return RiskAssessment(
            risk_level=RiskLevel.NEEDS_REVIEW,
            reasons=[
                f"跟踪置信度 {analysis.tracking_confidence:.2f} 低于阈值 "
                f"{settings.minimum_tracking_confidence:.2f}"
            ],
            evidence=evidence,
            thresholds=thresholds,
        )
    if not knowledge_sources:
        return RiskAssessment(
            risk_level=RiskLevel.NEEDS_REVIEW,
            reasons=["知识库未返回可引用的正式标准，结果仅可作为测量参考"],
            evidence=evidence,
            thresholds=thresholds,
        )

    amplitude = (
        max(
            value
            for value in (analysis.horizontal_amplitude_m, analysis.vertical_amplitude_m)
            if value is not None
        )
        if any(
            value is not None
            for value in (analysis.horizontal_amplitude_m, analysis.vertical_amplitude_m)
        )
        else None
    )
    if amplitude is None:
        return RiskAssessment(
            risk_level=RiskLevel.NEEDS_REVIEW,
            reasons=["视频缺少 pixels_per_meter 标定，不能将像素幅值用于风险定级"],
            evidence=evidence,
            thresholds=thresholds,
        )

    if amplitude >= settings.high_amplitude_m:
        level = RiskLevel.HIGH
    elif amplitude >= settings.medium_amplitude_m:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW
    return RiskAssessment(
        risk_level=level,
        authoritative=False,
        reasons=[
            f"最大测量幅值为 {amplitude:.3f} m",
            "风险等级由可配置工程阈值计算，需由巡检人员结合正式标准复核",
        ],
        evidence=evidence,
        thresholds=thresholds,
    )
