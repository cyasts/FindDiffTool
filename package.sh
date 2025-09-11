#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

TARGET=${1:-mac-arm64} # mac-arm64 | mac-x86_64 | mac-all | mac-universal
# Detect app dir (monorepo vs standalone)
if [ -f "pyside_app/main.py" ]; then
  APP_DIR="pyside_app"
else
  APP_DIR="."
fi
ICON_PATH="Icon.icns"
if [ ! -f "$ICON_PATH" ]; then
  ICON_PATH="$APP_DIR/i.icns"
fi
if [ ! -f "$ICON_PATH" ] && [ -f "i.icns" ]; then
  ICON_PATH="i.icns"
fi

ensure_tools_for_arch() {
  local VENV=$1
  local ARCH=$2
  local ARCH_CMD=( )
  if command -v arch >/dev/null 2>&1; then
    ARCH_CMD=(arch -${ARCH})
  fi
  if [ ! -d "$VENV" ]; then
    "${ARCH_CMD[@]}" python3 -m venv "$VENV"
  fi
  # shellcheck disable=SC1090
  source "$VENV/bin/activate"
  python -m pip install --upgrade pip setuptools wheel
  export PIP_ONLY_BINARY=:all:
  if [ -f "$APP_DIR/requirements.txt" ]; then
    pip install -r "$APP_DIR/requirements.txt"
  elif [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
  fi
  pip install "pyinstaller>=6.9,<7" "altgraph>=0.17.4" "macholib>=1.16.3"
}

build_mac_arch() {
  local ARCH=$1 # arm64 | x86_64
  local VENV=".venv-${ARCH}"
  ensure_tools_for_arch "$VENV" "$ARCH"

  local NAME="FindDiffEditor-${ARCH}"
  local PYI_ARGS=(
    --noconfirm
    --name "$NAME"
    --windowed
  )
  if [ -f "$ICON_PATH" ]; then
    PYI_ARGS+=(--icon "$ICON_PATH")
  fi

  local ENTRY="$APP_DIR/main.py"
  [ -f "$ENTRY" ] || ENTRY="main.py"
  pyinstaller "${PYI_ARGS[@]}" "$ENTRY"

  local APP_PATH="dist/${NAME}.app"
  if [ ! -d "$APP_PATH" ]; then
    echo "Build failed: $APP_PATH not found" >&2
    exit 1
  fi
  echo "Built app: $APP_PATH"

  local DMG_PATH="dist/${NAME}.dmg"
  hdiutil create -volname "$NAME" -srcfolder "$APP_PATH" -ov -format UDZO "$DMG_PATH"
  echo "Created DMG: $DMG_PATH"

  deactivate || true
}

build_mac_universal() {
  local VENV=".venv-universal"
  # Use host arch python; PyInstaller will create universal2 bootloader
  if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
  fi
  # shellcheck disable=SC1090
  source "$VENV/bin/activate"
  python -m pip install --upgrade pip setuptools wheel
  export PIP_ONLY_BINARY=:all:
  if [ -f "$APP_DIR/requirements.txt" ]; then
    pip install -r "$APP_DIR/requirements.txt"
  elif [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
  fi
  pip install "pyinstaller>=6.9,<7" "altgraph>=0.17.4" "macholib>=1.16.3"

  local NAME="FindDiffEditor-universal"
  local PYI_ARGS=(
    --noconfirm
    --name "$NAME"
    --windowed
    --target-arch universal2
    --exclude-module PIL._webp
  )
  if [ -f "$ICON_PATH" ]; then
    PYI_ARGS+=(--icon "$ICON_PATH")
  fi

  local ENTRY="$APP_DIR/main.py"
  [ -f "$ENTRY" ] || ENTRY="main.py"
  pyinstaller "${PYI_ARGS[@]}" "$ENTRY" || {
    echo "Universal build failed. Falling back to building per-arch apps (arm64 & x86_64)." >&2
    deactivate || true
    build_mac_arch arm64
    build_mac_arch x86_64
    return
  }

  local APP_PATH="dist/${NAME}.app"
  if [ ! -d "$APP_PATH" ]; then
    echo "Build failed: $APP_PATH not found" >&2
    exit 1
  fi
  echo "Built app: $APP_PATH"

  local DMG_PATH="dist/${NAME}.dmg"
  hdiutil create -volname "$NAME" -srcfolder "$APP_PATH" -ov -format UDZO "$DMG_PATH"
  echo "Created DMG: $DMG_PATH"

  deactivate || true
}

case "$TARGET" in
  mac-arm64)
    build_mac_arch arm64
    ;;
  mac-x86_64)
    build_mac_arch x86_64
    ;;
  mac-all)
    build_mac_arch arm64
    build_mac_arch x86_64
    ;;
  mac-universal)
    build_mac_universal
    ;;
  *)
    echo "Usage: $0 {mac-arm64|mac-x86_64|mac-all|mac-universal}" >&2
    exit 2
    ;;
 esac

echo "Done."
