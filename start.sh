#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
APP_URL="http://127.0.0.1:5173"
API_HEALTH_URL="http://127.0.0.1:8000/health"
NO_OPEN=false

usage() {
  cat <<'EOF'
Usage: ./start.sh [--no-open]

Prepares and starts the Job Application Assistant.

Options:
  --no-open  Do not open the app in the default browser.
  -h, --help Show this help message.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-open)
      NO_OPEN=true
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
  shift
done

say() {
  printf '\n%s\n' "$1"
}

require_command() {
  local command_name="$1"
  local install_hint="$2"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    echo "$install_hint" >&2
    exit 1
  fi
}

url_is_ready() {
  curl --silent --show-error --fail --max-time 2 "$1" >/dev/null 2>&1
}

port_is_in_use() {
  local port="$1"

  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  elif command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$port" >/dev/null 2>&1
  else
    return 1
  fi
}

open_app() {
  if [[ "$NO_OPEN" == true ]]; then
    return
  fi

  if command -v open >/dev/null 2>&1; then
    open "$APP_URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$APP_URL" >/dev/null 2>&1 || true
  fi
}

require_command uv "Install uv from https://docs.astral.sh/uv/getting-started/installation/"
require_command npm "Install the current Node.js LTS release from https://nodejs.org/"
require_command curl "Install curl, then run this launcher again."

if url_is_ready "$API_HEALTH_URL" && url_is_ready "$APP_URL"; then
  say "The Job Application Assistant is already running at $APP_URL"
  open_app
  exit 0
fi

if port_is_in_use 8000; then
  echo "Port 8000 is already in use by another program." >&2
  echo "Stop that program and run ./start.sh again." >&2
  exit 1
fi

if port_is_in_use 5173; then
  echo "Port 5173 is already in use by another program." >&2
  echo "Stop that program and run ./start.sh again." >&2
  exit 1
fi

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
  say "Created .env from the safe local defaults."
fi

say "Preparing the backend..."
(
  cd "$BACKEND_DIR"
  uv sync --all-groups
)

say "Checking the browser used for application automation..."
(
  cd "$BACKEND_DIR"
  uv run playwright install chromium
)

say "Applying database migrations..."
(
  cd "$BACKEND_DIR"
  uv run alembic upgrade head
)

if [[ ! -x "$FRONTEND_DIR/node_modules/.bin/vite" ]] || \
   [[ "$FRONTEND_DIR/package.json" -nt "$FRONTEND_DIR/node_modules/.package-lock.json" ]] || \
   [[ "$FRONTEND_DIR/package-lock.json" -nt "$FRONTEND_DIR/node_modules/.package-lock.json" ]]; then
  say "Installing frontend dependencies..."
  (
    cd "$FRONTEND_DIR"
    npm install
  )
fi

backend_pid=""
frontend_pid=""

cleanup() {
  local exit_status=$?
  trap - EXIT INT TERM

  if [[ -n "$backend_pid" ]] && kill -0 "$backend_pid" >/dev/null 2>&1; then
    kill "$backend_pid" >/dev/null 2>&1 || true
  fi
  if [[ -n "$frontend_pid" ]] && kill -0 "$frontend_pid" >/dev/null 2>&1; then
    kill "$frontend_pid" >/dev/null 2>&1 || true
  fi

  [[ -n "$backend_pid" ]] && wait "$backend_pid" 2>/dev/null || true
  [[ -n "$frontend_pid" ]] && wait "$frontend_pid" 2>/dev/null || true

  if [[ "$exit_status" -eq 0 || "$exit_status" -eq 130 || "$exit_status" -eq 143 ]]; then
    printf '\nJob Application Assistant stopped.\n'
  fi

  exit "$exit_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

say "Starting the backend and frontend..."
(
  cd "$BACKEND_DIR"
  exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
) &
backend_pid=$!

(
  cd "$FRONTEND_DIR"
  exec ./node_modules/.bin/vite --host 127.0.0.1 --port 5173
) &
frontend_pid=$!

backend_ready=false
frontend_ready=false

for _attempt in $(seq 1 90); do
  if [[ "$backend_ready" == false ]] && url_is_ready "$API_HEALTH_URL"; then
    backend_ready=true
  fi
  if [[ "$frontend_ready" == false ]] && url_is_ready "$APP_URL"; then
    frontend_ready=true
  fi

  if [[ "$backend_ready" == true && "$frontend_ready" == true ]]; then
    break
  fi

  if ! kill -0 "$backend_pid" >/dev/null 2>&1; then
    echo "The backend stopped before it became ready." >&2
    exit 1
  fi
  if ! kill -0 "$frontend_pid" >/dev/null 2>&1; then
    echo "The frontend stopped before it became ready." >&2
    exit 1
  fi

  sleep 1
done

if [[ "$backend_ready" != true || "$frontend_ready" != true ]]; then
  echo "Startup timed out. Review the errors above and run ./start.sh again." >&2
  exit 1
fi

say "Job Application Assistant is ready: $APP_URL"
echo "Keep this window open. Press Ctrl+C once to stop everything."
open_app

while kill -0 "$backend_pid" >/dev/null 2>&1 && kill -0 "$frontend_pid" >/dev/null 2>&1; do
  sleep 1
done

echo "One of the application servers stopped unexpectedly." >&2
exit 1
