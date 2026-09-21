# Validation Status

Validated on Windows on 2026-09-21:

- `uv sync --frozen`
- `uv run pytest -q`: 144 passed, 2 skipped
- `uv run ruff format --check .` and `uv run ruff check .`
- `uv run mypy src/`: 65 source files passed
- FastAPI + Streamlit browser smoke test: task creation, plan approval, three-UAV
  dry-run execution, telemetry chart/table, and report approval checkpoint
- Synthetic OpenCV frequency and amplitude assertions, including evidence-frame
  output under a Windows path containing non-ASCII characters
- SQLite migration and end-to-end API workflow
- XML parsing of the transmission-line Gazebo world
- PowerShell syntax parsing of the WSL launcher
- `git diff --check`

Implemented but not executed in the current environment:

- Alembic migration against PostgreSQL
- Docker Compose startup
- Three-instance PX4 SITL and Gazebo startup
- MAVSDK Offboard execution against UDP ports `14540-14542`

The remaining blockers are environmental: Docker, Gazebo, and a usable Ubuntu WSL
distribution are unavailable on this machine. The optional MAVSDK dependency and
live PX4 instances were not part of this validation run.

Local dry-run:

```powershell
uv sync --group uav
uv run pytest tests/lineguard -v
uv run python scripts/run_lineguard.py --uav-backend dryrun
```

Then run `scripts/run_px4_gazebo_wsl.ps1` in a configured WSL/PX4 environment and
complete one demonstration using a real inspection PDF and video. Do not claim PX4
flight validation or production deployment until the corresponding commands pass.
