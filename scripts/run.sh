#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
CONFIG_PATH=${1:-"$ROOT_DIR/config.json"}

if [[ ! -d "$ROOT_DIR/.venv" ]]; then
  echo "Missing .venv. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

source "$ROOT_DIR/.venv/bin/activate"
python -m app.main --config "$CONFIG_PATH"
