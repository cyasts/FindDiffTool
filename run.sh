#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
export QT_DEBUG_PLUGINS=0

# Always run from the script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure virtual environment
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1090
source .venv/bin/activate

# Ensure base tooling
python -m pip install --upgrade pip setuptools wheel >/dev/null

# Ensure required dependencies
if ! python - <<'PY'
import importlib.util
mods = ["PySide6", "PIL", "requests"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
raise SystemExit(1 if missing else 0)
PY
then
  python -m pip install -r requirements.txt
fi

# Run the app
exec python3 main.py
