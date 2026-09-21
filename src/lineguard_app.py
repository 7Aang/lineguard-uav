from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

API_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8080").rstrip("/")
AUTH_SECRET = os.getenv("AUTH_SECRET")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AUTH_SECRET}"} if AUTH_SECRET else {}


def _request(method: str, path: str, **kwargs: Any) -> Any:
    headers = {**_headers(), **kwargs.pop("headers", {})}
    response = httpx.request(
        method,
        f"{API_URL}{path}",
        headers=headers,
        timeout=120,
        **kwargs,
    )
    if response.is_error:
        raise RuntimeError(f"{response.status_code}: {response.text}")
    return response.json()


def _artifact_url(path: str | None) -> str | None:
    if not path:
        return None
    parts = Path(path).parts
    try:
        index = parts.index("artifacts")
    except ValueError:
        return None
    return f"{API_URL}/lineguard-artifacts/{'/'.join(parts[index + 1 :])}"


def task_submission_page() -> None:
    st.header("任务提交")
    st.caption("先上传巡检报告和视频，再提交自然语言巡检任务。")
    pdf = st.file_uploader("巡检报告 PDF", type=["pdf"])
    video = st.file_uploader("舞动视频", type=["mp4", "avi", "mov", "mkv"])
    pixels_per_meter = st.number_input(
        "标定比例 pixels_per_meter（可选）",
        min_value=0.0,
        value=0.0,
        step=1.0,
    )
    roi = st.text_input("ROI JSON（可选）", placeholder="[x, y, width, height]")
    prompt = st.text_area(
        "巡检任务",
        value=(
            "对 220kV 某输电线路 3 号到 5 号塔之间进行强风工况下的"
            "导地线舞动巡检，重点观察间隔棒和导线舞动幅值，任务完成后生成风险报告。"
        ),
        height=130,
    )
    if st.button("上传素材并提交任务", type="primary"):
        asset_ids: list[str] = []
        try:
            for uploaded, asset_type in ((pdf, "pdf"), (video, "video")):
                if uploaded is None:
                    continue
                data: dict[str, str] = {"asset_type": asset_type}
                if asset_type == "video" and pixels_per_meter > 0:
                    data["pixels_per_meter"] = str(pixels_per_meter)
                if asset_type == "video" and roi.strip():
                    json.loads(roi)
                    data["roi_json"] = roi
                asset = _request(
                    "POST",
                    "/api/assets",
                    data=data,
                    files={
                        "file": (
                            uploaded.name,
                            uploaded.getvalue(),
                            uploaded.type or "application/octet-stream",
                        )
                    },
                )
                asset_ids.append(asset["id"])
                st.success(f"已上传 {asset['filename']}，状态：{asset['status']}")
            task = _request(
                "POST",
                "/api/tasks",
                json={"prompt": prompt, "asset_ids": asset_ids},
            )
            st.session_state["lineguard_task_id"] = task["id"]
            st.success(f"任务已创建：{task['id']}")
            st.json(task)
        except Exception as exc:
            st.error(str(exc))


def review_page() -> None:
    st.header("人工审批")
    task_id = st.text_input(
        "任务 ID",
        value=st.session_state.get("lineguard_task_id", ""),
        key="review_task_id",
    )
    if not task_id:
        return
    try:
        task = _request("GET", f"/api/tasks/{task_id}")
    except Exception as exc:
        st.error(str(exc))
        return
    st.write(f"当前状态：`{task['status']}`")
    if task.get("mission_plan"):
        st.subheader("多无人机计划")
        st.json(task["mission_plan"])
    if task.get("mission_execution"):
        st.subheader("无人机执行")
        st.json(task["mission_execution"])
    if task.get("risk_assessment"):
        st.subheader("风险判断")
        st.json(task["risk_assessment"])
    stage = None
    if task["status"] == "awaiting_plan_approval":
        stage = "plan"
    elif task["status"] == "awaiting_report_approval":
        stage = "report"
    if not stage:
        st.info("当前没有待审批节点。")
        return
    reviewer = st.text_input("审批人", value="demo-reviewer")
    reason = st.text_input("审批说明")
    approve, reject = st.columns(2)
    try:
        if approve.button("批准", type="primary"):
            result = _request(
                "POST",
                f"/api/tasks/{task_id}/reviews",
                json={
                    "stage": stage,
                    "decision": "approve",
                    "reviewer": reviewer,
                    "reason": reason or None,
                },
            )
            st.success(f"审批完成，状态：{result['status']}")
            st.rerun()
        if reject.button("驳回"):
            result = _request(
                "POST",
                f"/api/tasks/{task_id}/reviews",
                json={
                    "stage": stage,
                    "decision": "reject",
                    "reviewer": reviewer,
                    "reason": reason or "人工驳回",
                },
            )
            st.warning(f"任务已驳回：{result['error']}")
            st.rerun()
    except Exception as exc:
        st.error(str(exc))


def trace_page() -> None:
    st.header("Agent Trace")
    task_id = st.text_input(
        "任务 ID",
        value=st.session_state.get("lineguard_task_id", ""),
        key="trace_task_id",
    )
    if st.button("加载 Trace") and task_id:
        try:
            trace = _request("GET", f"/api/tasks/{task_id}/trace")
            st.dataframe(trace, use_container_width=True)
            with st.expander("完整 JSON"):
                st.json(trace)
        except Exception as exc:
            st.error(str(exc))


def simulation_page() -> None:
    st.header("飞行仿真")
    st.caption("dry-run 与 PX4/Gazebo 后端共享相同的任务和遥测数据合同。")
    task_id = st.text_input(
        "任务 ID",
        value=st.session_state.get("lineguard_task_id", ""),
        key="simulation_task_id",
    )
    if st.button("加载飞行遥测", type="primary") and task_id:
        try:
            st.session_state["mission_execution"] = _request(
                "GET",
                f"/api/tasks/{task_id}/telemetry",
            )
            st.session_state["mission_execution_task_id"] = task_id
            st.session_state.pop("mission_execution_error", None)
        except Exception as exc:
            st.session_state["mission_execution_error"] = str(exc)

    if error := st.session_state.get("mission_execution_error"):
        st.error(error)
    if st.session_state.get("mission_execution_task_id") != task_id:
        return
    execution = st.session_state.get("mission_execution")
    if not execution:
        return
    st.write(
        f"后端：`{execution['backend']}`　状态：`{execution['status']}`　"
        f"安全校验：`{execution['safety_check']['approved']}`"
    )
    if execution.get("failure_reason"):
        st.error(execution["failure_reason"])
    rows = [
        sample
        for vehicle in execution.get("vehicles", [])
        for sample in vehicle.get("telemetry", [])
    ]
    if not rows:
        st.info("当前执行没有遥测样本。")
        return
    telemetry_frame = pd.DataFrame(rows)
    st.subheader("水平航迹")
    figure, axes = plt.subplots(figsize=(9, 4.5))
    for uav_id, samples in telemetry_frame.groupby("uav_id", sort=True):
        ordered = samples.sort_values("sequence")
        axes.plot(
            ordered["x_m"],
            ordered["y_m"],
            marker=".",
            linewidth=1.5,
            label=str(uav_id),
        )
    axes.set_xlabel("ENU East / m")
    axes.set_ylabel("ENU North / m")
    axes.grid(alpha=0.3)
    axes.legend(title="UAV")
    figure.tight_layout()
    st.pyplot(figure, width="stretch")
    plt.close(figure)
    st.subheader("遥测明细")
    st.dataframe(telemetry_frame, width="stretch")


def analysis_page() -> None:
    st.header("视频分析")
    asset_id = st.text_input("视频 Asset ID")
    task_id = st.text_input(
        "关联任务 ID（可选）",
        value=st.session_state.get("lineguard_task_id", ""),
        key="analysis_task_id",
    )
    if st.button("运行 OpenCV 分析", type="primary") and asset_id:
        try:
            result = _request(
                "POST",
                "/api/videos/analyze",
                json={"asset_id": asset_id, "task_id": task_id or None},
            )
            st.json(result)
            plot_url = _artifact_url(result.get("artifacts", {}).get("displacement_plot"))
            evidence_url = _artifact_url(result.get("artifacts", {}).get("evidence_frame"))
            if plot_url:
                st.image(plot_url, caption="位移时程")
            if evidence_url:
                st.image(evidence_url, caption="证据帧")
        except Exception as exc:
            st.error(str(exc))


def report_page() -> None:
    st.header("巡检报告")
    task_id = st.text_input(
        "任务 ID",
        value=st.session_state.get("lineguard_task_id", ""),
        key="report_task_id",
    )
    if st.button("加载报告") and task_id:
        try:
            task = _request("GET", f"/api/tasks/{task_id}")
            if not task.get("report_id"):
                st.info("任务尚未生成报告。")
                return
            report = _request("GET", f"/api/reports/{task['report_id']}")
            st.subheader(report["summary"])
            st.json(report["payload"])
            for name, path in report.get("artifact_urls", {}).items():
                st.link_button(f"打开 {name}", f"{API_URL}{path}")
            st.download_button(
                "下载报告 JSON",
                data=json.dumps(report["payload"], ensure_ascii=False, indent=2),
                file_name=f"lineguard-{task_id}.json",
                mime="application/json",
            )
        except Exception as exc:
            st.error(str(exc))


def main() -> None:
    st.set_page_config(page_title="LineGuard-Agent", page_icon="LG", layout="wide")
    st.title("LineGuard-Agent")
    st.caption("输电线路多无人机任务规划与异常诊断 Agent 平台")
    page = st.sidebar.radio(
        "功能",
        [
            "任务提交",
            "人工审批",
            "飞行仿真",
            "Agent Trace",
            "视频分析",
            "巡检报告",
        ],
    )
    {
        "任务提交": task_submission_page,
        "人工审批": review_page,
        "飞行仿真": simulation_page,
        "Agent Trace": trace_page,
        "视频分析": analysis_page,
        "巡检报告": report_page,
    }[page]()


if __name__ == "__main__":
    main()
