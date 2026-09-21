from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the LineGuard API and UI.")
    parser.add_argument(
        "--uav-backend",
        choices=("dryrun", "mavsdk"),
        default=os.getenv("LINEGUARD_UAV_BACKEND", "dryrun"),
        help="Use deterministic local telemetry or connect to PX4 through MAVSDK.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    if not any(
        environment.get(name)
        for name in (
            "OPENAI_API_KEY",
            "COMPATIBLE_API_KEY",
            "DEEPSEEK_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
        )
    ):
        environment["USE_FAKE_MODEL"] = "true"
    environment.setdefault("LINEGUARD_USE_LLM", "false")
    environment["LINEGUARD_UAV_BACKEND"] = arguments.uav_backend

    commands = [
        [sys.executable, str(root / "src" / "run_service.py")],
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(root / "src" / "lineguard_app.py"),
            "--server.port",
            "8501",
        ],
    ]
    processes = [subprocess.Popen(command, cwd=root, env=environment) for command in commands]
    print("LineGuard API: http://localhost:8080/docs")
    print("LineGuard UI:  http://localhost:8501")
    print(f"UAV backend:   {arguments.uav_backend}")
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    return max((process.returncode or 0) for process in processes)


if __name__ == "__main__":
    raise SystemExit(main())
