#!/usr/bin/env bash
# Start backend and frontend together for local development.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$root/backend/.env" ]; then
  echo "backend/.env is missing. Copy backend/.env.example and point DB_URL somewhere." >&2
  exit 1
fi

# The CLI would otherwise open an interactive analytics prompt on first run,
# which cannot be answered when both servers share this terminal.
export NG_CLI_ANALYTICS=false

cleanup() {
  # Kill the whole process group so uvicorn's reloader dies with us.
  kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(cd "$root/backend" && uv run uvicorn app.main:app --reload --port 8000) &
(cd "$root/frontend" && npm start) &

echo ""
echo "  App:      http://localhost:4200   <- open this one"
echo "  API docs: http://localhost:8000/docs"
echo ""

wait
