#!/usr/bin/env bash
# AgentQon — one-command start
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
echo "AgentQon starting at http://127.0.0.1:8077"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8077
