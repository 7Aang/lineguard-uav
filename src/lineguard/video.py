from __future__ import annotations

import csv
import math

import cv2
import matplotlib
import numpy as np

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

from lineguard.audit import audited_tool
from lineguard.config import get_settings
from lineguard.models import AnalysisStatus, VideoAnalysis, VideoArtifacts
from lineguard.repository import get_asset


def _failed(asset_id: str, reason: str) -> VideoAnalysis:
    return VideoAnalysis(
        status=AnalysisStatus.FAILED,
        asset_id=asset_id,
        failure_reason=reason,
    )


def _dominant_frequency(values: np.ndarray, fps: float) -> float | None:
    if len(values) < 8 or fps <= 0:
        return None
    centered = values - np.mean(values)
    if float(np.std(centered)) < 1e-6:
        return 0.0
    spectrum = np.abs(np.fft.rfft(centered))
    frequencies = np.fft.rfftfreq(len(centered), d=1.0 / fps)
    if len(spectrum) <= 1:
        return None
    index = int(np.argmax(spectrum[1:]) + 1)
    return float(frequencies[index])


def _ellipse_angle(x_values: np.ndarray, y_values: np.ndarray) -> float | None:
    if len(x_values) < 3:
        return None
    covariance = np.cov(np.column_stack((x_values, y_values)), rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    vector = eigenvectors[:, int(np.argmax(eigenvalues))]
    return float(math.degrees(math.atan2(vector[1], vector[0])))


@audited_tool("analyze_galloping_video", max_retries=1)
def analyze_galloping_video(task_id: str | None, asset_id: str) -> VideoAnalysis:
    asset = get_asset(asset_id)
    if asset is None:
        raise KeyError(asset_id)
    capture = cv2.VideoCapture(asset.storage_path)
    if not capture.isOpened():
        return _failed(asset_id, "VIDEO_OPEN_FAILED")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    expected_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    ok, first_frame = capture.read()
    if not ok or first_frame is None:
        capture.release()
        return _failed(asset_id, "VIDEO_HAS_NO_READABLE_FRAMES")

    height, width = first_frame.shape[:2]
    roi = asset.calibration.get("roi") if asset.calibration else None
    if roi and len(roi) == 4:
        x, y, roi_width, roi_height = [int(value) for value in roi]
        x = max(0, min(x, width - 1))
        y = max(0, min(y, height - 1))
        roi_width = max(1, min(roi_width, width - x))
        roi_height = max(1, min(roi_height, height - y))
    else:
        x, y, roi_width, roi_height = 0, 0, width, height

    first_gray = cv2.cvtColor(first_frame, cv2.COLOR_BGR2GRAY)
    roi_gray = first_gray[y : y + roi_height, x : x + roi_width]
    raw_points = cv2.goodFeaturesToTrack(
        roi_gray,
        maxCorners=120,
        qualityLevel=0.01,
        minDistance=5,
        blockSize=7,
    )
    if raw_points is None or len(raw_points) < 3:
        capture.release()
        return _failed(asset_id, "NO_TRACKABLE_FEATURES_IN_ROI")
    points = np.asarray(raw_points, dtype=np.float32).copy()
    points[:, 0, 0] = points[:, 0, 0] + x
    points[:, 0, 1] = points[:, 0, 1] + y

    initial_feature_count = len(points)
    previous_gray = first_gray
    initial_centroid = np.mean(points[:, 0, :], axis=0)
    displacements = [(0.0, 0.0)]
    frame_numbers = [0]
    valid_frames = 1
    total_frames = 1
    evidence_frame = first_frame.copy()

    while True:
        ok, frame = capture.read()
        if not ok or frame is None:
            break
        total_frames += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        next_points, status, _ = cv2.calcOpticalFlowPyrLK(
            previous_gray,
            gray,
            points,
            np.empty_like(points),
            winSize=(31, 31),
            maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
        )
        if next_points is None or status is None:
            previous_gray = gray
            continue
        good_new = next_points[status.flatten() == 1].reshape(-1, 2)
        if len(good_new) < 3:
            previous_gray = gray
            continue

        centroid = np.mean(good_new, axis=0)
        displacement = centroid - initial_centroid
        displacements.append((float(displacement[0]), float(displacement[1])))
        frame_numbers.append(total_frames - 1)
        valid_frames += 1
        points = good_new.reshape(-1, 1, 2)
        previous_gray = gray
        evidence_frame = frame.copy()

    capture.release()
    if len(displacements) < 8:
        return _failed(asset_id, "INSUFFICIENT_TRACKED_FRAMES")

    array = np.asarray(displacements, dtype=np.float64)
    x_values = array[:, 0]
    y_values = array[:, 1]
    horizontal_amplitude_px = float((np.max(x_values) - np.min(x_values)) / 2.0)
    vertical_amplitude_px = float((np.max(y_values) - np.min(y_values)) / 2.0)
    effective_fps = fps if fps > 0 else 30.0
    signal = y_values if np.std(y_values) >= np.std(x_values) else x_values
    observed_frames = np.asarray(frame_numbers, dtype=np.float64)
    uniform_frames = np.arange(observed_frames[0], observed_frames[-1] + 1)
    uniform_signal = np.interp(uniform_frames, observed_frames, signal)
    frequency = _dominant_frequency(uniform_signal, effective_fps)
    angle = _ellipse_angle(x_values, y_values)
    confidence = min(
        1.0,
        (valid_frames / max(total_frames, expected_frames, 1))
        * min(1.0, len(points) / max(10.0, initial_feature_count * 0.5)),
    )

    pixels_per_meter = None
    if asset.calibration:
        raw_scale = asset.calibration.get("pixels_per_meter")
        if raw_scale is not None and float(raw_scale) > 0:
            pixels_per_meter = float(raw_scale)

    run_name = task_id or f"standalone-{asset_id}"
    artifact_dir = get_settings().artifacts_dir / run_name
    artifact_dir.mkdir(parents=True, exist_ok=True)
    csv_path = artifact_dir / "displacement.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["frame", "time_s", "x_px", "y_px"])
        for frame_number, (x_value, y_value) in zip(frame_numbers, displacements, strict=False):
            writer.writerow([frame_number, frame_number / effective_fps, x_value, y_value])

    plot_path = artifact_dir / "displacement.png"
    times = np.asarray(frame_numbers) / effective_fps
    figure, axis = plt.subplots(figsize=(8, 4))
    axis.plot(times, x_values, label="horizontal_px")
    axis.plot(times, y_values, label="vertical_px")
    axis.set_xlabel("time_s")
    axis.set_ylabel("displacement_px")
    axis.grid(alpha=0.3)
    axis.legend()
    figure.tight_layout()
    figure.savefig(plot_path, dpi=140)
    plt.close(figure)

    evidence_path = artifact_dir / "evidence.jpg"
    cv2.rectangle(
        evidence_frame,
        (x, y),
        (x + roi_width, y + roi_height),
        (0, 255, 0),
        2,
    )
    for point in points[:, 0, :]:
        cv2.circle(evidence_frame, tuple(np.round(point).astype(int)), 2, (0, 0, 255), -1)
    encoded, buffer = cv2.imencode(".jpg", evidence_frame)
    if not encoded:
        return _failed(asset_id, "EVIDENCE_FRAME_ENCODING_FAILED")
    evidence_path.write_bytes(buffer.tobytes())

    return VideoAnalysis(
        status=AnalysisStatus.COMPLETED,
        asset_id=asset_id,
        frame_count=total_frames,
        valid_frame_count=valid_frames,
        fps=effective_fps,
        horizontal_amplitude_px=horizontal_amplitude_px,
        vertical_amplitude_px=vertical_amplitude_px,
        horizontal_amplitude_m=(
            horizontal_amplitude_px / pixels_per_meter if pixels_per_meter else None
        ),
        vertical_amplitude_m=(
            vertical_amplitude_px / pixels_per_meter if pixels_per_meter else None
        ),
        frequency_hz=frequency,
        ellipse_angle_deg=angle,
        tracking_confidence=round(confidence, 4),
        artifacts=VideoArtifacts(
            evidence_frame=str(evidence_path),
            displacement_csv=str(csv_path),
            displacement_plot=str(plot_path),
        ),
    )
