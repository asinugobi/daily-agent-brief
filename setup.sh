#!/usr/bin/env bash
# Creates the virtualenv and installs the Agent SDK. Run once, after python@3.12 is installed.
set -euo pipefail
cd "$(dirname "$0")"

PY="$(command -v python3.12 || command -v python3.13 || true)"
if [ -z "$PY" ]; then
  echo "No python3.12 found. Run: brew install python@3.12" >&2
  exit 1
fi

"$PY" -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt
echo "Ready. Python: $("$PY" --version)"
echo "SDK: $(./.venv/bin/pip show claude-agent-sdk | sed -n 's/^Version: /claude-agent-sdk /p')"
