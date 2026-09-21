#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PX4_DIR="${PX4_DIR:-$HOME/PX4-Autopilot}"
VEHICLE_COUNT=3
HEADLESS=0
START_AGENT=1

usage() {
  cat <<'EOF'
Usage: scripts/run_px4_gazebo_wsl.sh [options]

Options:
  --px4-dir PATH       PX4-Autopilot checkout (default: ~/PX4-Autopilot)
  --vehicles N         Number of PX4 x500 instances (default: 3)
  --headless           Start Gazebo server without its GUI
  --no-agent           Start only Gazebo and PX4 instances
  -h, --help           Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --px4-dir)
      PX4_DIR="$2"
      shift 2
      ;;
    --vehicles)
      VEHICLE_COUNT="$2"
      shift 2
      ;;
    --headless)
      HEADLESS=1
      shift
      ;;
    --no-agent)
      START_AGENT=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v gz >/dev/null 2>&1; then
  echo "Gazebo Sim command 'gz' is unavailable in this WSL distribution." >&2
  exit 1
fi
if [[ ! -d "$PX4_DIR" ]]; then
  echo "PX4 checkout not found: $PX4_DIR" >&2
  exit 1
fi
if ! [[ "$VEHICLE_COUNT" =~ ^[1-3]$ ]]; then
  echo "--vehicles must be between 1 and 3 for this MVP." >&2
  exit 2
fi

PX4_BIN="$PX4_DIR/build/px4_sitl_default/bin/px4"
if [[ ! -x "$PX4_BIN" ]]; then
  echo "Building PX4 SITL because $PX4_BIN does not exist."
  make -C "$PX4_DIR" px4_sitl_default
fi

WORLD="$PROJECT_ROOT/simulation/worlds/lineguard_powerline.sdf"
LOG_DIR="$PROJECT_ROOT/logs/px4"
mkdir -p "$LOG_DIR"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
PIDS=()

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  wait >/dev/null 2>&1 || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

echo "Starting Gazebo world: $WORLD"
if [[ "$HEADLESS" -eq 1 ]]; then
  gz sim -r -s "$WORLD" >"$LOG_DIR/gazebo_${RUN_ID}.log" 2>&1 &
else
  gz sim -r "$WORLD" >"$LOG_DIR/gazebo_${RUN_ID}.log" 2>&1 &
fi
PIDS+=("$!")
sleep 5
if ! kill -0 "${PIDS[0]}" >/dev/null 2>&1; then
  echo "Gazebo exited during startup. Inspect $LOG_DIR/gazebo_${RUN_ID}.log" >&2
  exit 1
fi

spawn_poses=("0,-12,0,0,0,0" "0,0,0,0,0,0" "0,12,0,0,0,0")
for ((instance=0; instance<VEHICLE_COUNT; instance++)); do
  log_path="$LOG_DIR/px4_${instance}_${RUN_ID}.log"
  echo "Starting PX4 instance $instance -> UDP $((14540 + instance))"
  (
    cd "$PX4_DIR"
    PX4_GZ_STANDALONE=1 \
    PX4_SYS_AUTOSTART=4001 \
    PX4_SIM_MODEL=gz_x500 \
    PX4_GZ_MODEL_POSE="${spawn_poses[$instance]}" \
    "$PX4_BIN" -i "$instance"
  ) >"$log_path" 2>&1 &
  PIDS+=("$!")
done

sleep 8
for ((index=1; index<${#PIDS[@]}; index++)); do
  if ! kill -0 "${PIDS[$index]}" >/dev/null 2>&1; then
    instance=$((index - 1))
    echo "PX4 instance $instance exited. Inspect its log under $LOG_DIR." >&2
    exit 1
  fi
done

echo "PX4/Gazebo is ready. Logs: $LOG_DIR"
if [[ "$START_AGENT" -eq 1 ]]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to start LineGuard. Run the Agent separately or install uv." >&2
    exit 1
  fi
  if ! uv run --group uav python -c "import mavsdk" >/dev/null 2>&1; then
    echo "MAVSDK is unavailable. Run: uv sync --group uav" >&2
    exit 1
  fi
  cd "$PROJECT_ROOT"
  uv run --group uav python scripts/run_lineguard.py --uav-backend mavsdk
else
  echo "Start the Agent with:"
  echo "  uv run --group uav python scripts/run_lineguard.py --uav-backend mavsdk"
  wait
fi
