from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from lineguard.repository import create_asset, create_task
from lineguard.video import analyze_galloping_video


def _write_synthetic_video(
    path: Path,
    frequency_hz: float,
    amplitude_px: float,
    fps: float = 30.0,
    duration_s: float = 10.0,
) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        fps,
        (320, 240),
    )
    if not writer.isOpened():
        pytest.skip("MJPG video writer is unavailable")
    base_points = [(x, y) for x in range(80, 241, 32) for y in range(90, 151, 20)]
    for frame_index in range(int(fps * duration_s)):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        offset = amplitude_px * math.sin(2 * math.pi * frequency_hz * frame_index / fps)
        for x, y in base_points:
            cv2.circle(frame, (x, int(round(y + offset))), 4, (255, 255, 255), -1)
        writer.write(frame)
    writer.release()


def test_synthetic_video_frequency_and_amplitude(lineguard_runtime: Path) -> None:
    video_path = lineguard_runtime / "synthetic.avi"
    expected_frequency = 0.8
    expected_amplitude = 12.0
    _write_synthetic_video(video_path, expected_frequency, expected_amplitude)

    task = create_task("synthetic video test", [])
    asset = create_asset(
        filename="synthetic.avi",
        asset_type="video",
        storage_path=str(video_path),
        content_type="video/x-msvideo",
        size_bytes=video_path.stat().st_size,
        status="stored",
        calibration={"pixels_per_meter": 20.0, "roi": [50, 60, 220, 120]},
    )
    analysis = analyze_galloping_video(task.id, asset.id)

    assert analysis.status == "completed"
    assert analysis.frequency_hz is not None
    assert analysis.vertical_amplitude_px is not None
    assert abs(analysis.frequency_hz - expected_frequency) / expected_frequency < 0.10
    assert abs(analysis.vertical_amplitude_px - expected_amplitude) / expected_amplitude < 0.10
    assert analysis.vertical_amplitude_m == pytest.approx(0.6, rel=0.10)
    assert analysis.tracking_confidence >= 0.60
    assert Path(analysis.artifacts.displacement_csv).exists()
    assert Path(analysis.artifacts.displacement_plot).exists()
    assert Path(analysis.artifacts.evidence_frame).exists()
