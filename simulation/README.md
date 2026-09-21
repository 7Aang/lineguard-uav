# PX4/Gazebo Simulation

The simulation layer is additive:

- `dryrun` is the default and produces deterministic three-UAV telemetry without PX4.
- `mavsdk` connects the same mission plan to three PX4 SITL instances through UDP
  ports `14540`, `14541`, and `14542`.
- Internal mission coordinates are local ENU. Conversion to PX4 local NED happens
  only in `lineguard.safety.enu_to_ned`.
- Every mission is checked for altitude, local geofence, speed, and inter-UAV
  separation before arming.

## WSL prerequisites

Install an Ubuntu WSL distribution, PX4-Autopilot with its Gazebo dependencies,
Gazebo Sim (`gz`), and `uv`. Then install the optional Python dependency:

```bash
uv sync --group uav
```

Start the complete stack from PowerShell:

```powershell
.\scripts\run_px4_gazebo_wsl.ps1 -Px4Dir "~/PX4-Autopilot"
```

Or inside WSL:

```bash
./scripts/run_px4_gazebo_wsl.sh --px4-dir ~/PX4-Autopilot
```

Use `--headless` for a server-only Gazebo process and `--no-agent` to keep only
Gazebo/PX4 running. Startup failures are written to `logs/px4/`; inspect those logs
before debugging MAVSDK connection timeouts.
