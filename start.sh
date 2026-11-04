#!/usr/bin/env bash
# Start VOCE:  ./start.sh [sim|ursim|real]   (default: ROBOT_MODE or ursim)
set -euo pipefail
cd "$(dirname "$0")"
MODE="${1:-${ROBOT_MODE:-ursim}}"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt -e .
if [ ! -f frontend/dist/index.html ]; then
  (cd frontend && npm install --no-audit --no-fund && npm run build)
fi
[ -f .env ] || { echo "Missing .env (copy .env.example and set XAI_API_KEY). Text mode works without it."; }
exec .venv/bin/python -m app.launcher --mode "$MODE"
