from __future__ import annotations

import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def benchmark_metadata(root: Path) -> dict[str, str]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    return {
        "schema_version": "lineguard-benchmark-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": commit,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "status": "COMPLETED",
    }
