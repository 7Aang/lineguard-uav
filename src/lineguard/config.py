from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from lineguard.models import UAVBackend


@dataclass(frozen=True)
class LineGuardSettings:
    data_dir: Path
    database_url: str
    chroma_dir: Path
    artifacts_dir: Path
    assets_dir: Path
    use_llm: bool
    medium_amplitude_m: float
    high_amplitude_m: float
    minimum_tracking_confidence: float
    uav_backend: UAVBackend
    px4_connections: tuple[str, ...]
    maximum_altitude_m: float
    maximum_radius_m: float
    minimum_uav_separation_m: float
    waypoint_tolerance_m: float
    waypoint_timeout_s: float
    dryrun_realtime: bool
    duplicate_wait_timeout_s: float


def _px4_connections() -> tuple[str, ...]:
    raw = os.getenv("LINEGUARD_PX4_CONNECTIONS")
    if not raw:
        return (
            "udpin://0.0.0.0:14540",
            "udpin://0.0.0.0:14541",
            "udpin://0.0.0.0:14542",
        )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [value.strip() for value in raw.split(",")]
    if not isinstance(parsed, list) or not all(isinstance(value, str) for value in parsed):
        raise ValueError("LINEGUARD_PX4_CONNECTIONS must be a JSON array or CSV string")
    return tuple(value for value in parsed if value)


def get_settings() -> LineGuardSettings:
    data_dir = Path(os.getenv("LINEGUARD_DATA_DIR") or "data/lineguard").resolve()
    database_url = os.getenv("LINEGUARD_DATABASE_URL") or (
        f"sqlite:///{(data_dir / 'lineguard.db').as_posix()}"
    )
    return LineGuardSettings(
        data_dir=data_dir,
        database_url=database_url,
        chroma_dir=data_dir / "chroma",
        artifacts_dir=data_dir / "artifacts",
        assets_dir=data_dir / "assets",
        use_llm=os.getenv("LINEGUARD_USE_LLM", "false").lower() in {"1", "true", "yes"},
        medium_amplitude_m=float(os.getenv("LINEGUARD_MEDIUM_AMPLITUDE_M", "0.30")),
        high_amplitude_m=float(os.getenv("LINEGUARD_HIGH_AMPLITUDE_M", "0.80")),
        minimum_tracking_confidence=float(os.getenv("LINEGUARD_MIN_TRACKING_CONFIDENCE", "0.60")),
        uav_backend=UAVBackend(os.getenv("LINEGUARD_UAV_BACKEND", "dryrun").lower()),
        px4_connections=_px4_connections(),
        maximum_altitude_m=float(os.getenv("LINEGUARD_MAX_ALTITUDE_M", "60")),
        maximum_radius_m=float(os.getenv("LINEGUARD_MAX_RADIUS_M", "300")),
        minimum_uav_separation_m=float(os.getenv("LINEGUARD_MIN_UAV_SEPARATION_M", "5")),
        waypoint_tolerance_m=float(os.getenv("LINEGUARD_WAYPOINT_TOLERANCE_M", "1.5")),
        waypoint_timeout_s=float(os.getenv("LINEGUARD_WAYPOINT_TIMEOUT_S", "45")),
        dryrun_realtime=os.getenv("LINEGUARD_DRYRUN_REALTIME", "false").lower()
        in {"1", "true", "yes"},
        duplicate_wait_timeout_s=float(os.getenv("LINEGUARD_DUPLICATE_WAIT_TIMEOUT_S", "5")),
    )


def ensure_runtime_directories() -> LineGuardSettings:
    settings = get_settings()
    for path in (
        settings.data_dir,
        settings.chroma_dir,
        settings.artifacts_dir,
        settings.assets_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return settings
