#!/usr/bin/env bash
set -euo pipefail

# ================== 基本配置 ==================
APP_NAME=FindCatTool
ENTRY_FILE=main.py         # 如果放在子目录，改成 pyside_app/main.py
ICON_FILE=Icon.icns        # 可不存在；不存在则忽略
REQ_FILE=requirements.txt  # 依赖文件
VERSION_FILE=version.py

# ================== 工具函数 ==================
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"

have_cmd() { command -v "$1" >/dev/null 2>&1; }

ensure_rosetta_for_x86() {
  if [[ "$(uname -m)" == "arm64" ]]; then
    if ! /usr/bin/pgrep oahd >/dev/null 2>&1; then
      echo "需要 Rosetta 才能构建 x86_64。正在检查…"
      sudo softwareupdate --install-rosetta --agree-to-license || true
    fi
  fi
}

venv_for_arch() {
  local arch="$1"       # arm64 | x86_64
  echo ".venv-${arch}"
}

py_for_arch() {
  local arch="$1"
  if have_cmd arch; then
    echo "arch -${arch} python3"
  else
    echo "python3"
  fi
}

pip_for_arch() {
  local arch="$1"
  if have_cmd arch; then
    echo "arch -${arch} python3 -m pip"
  else
    echo "python3 -m pip"
  fi
}

install_tools_and_deps() {
  local arch="$1"
  local venv="$(venv_for_arch "$arch")"
  if [[ ! -d "$venv" ]]; then
    eval "$(py_for_arch "$arch") -m venv \"$venv\""
  fi
  # shellcheck disable=SC1090
  source "$venv/bin/activate"
  python -m pip install --upgrade pip setuptools wheel

  # 避免源码编译（更快更稳）
  export PIP_ONLY_BINARY=:all:

  # 安装项目依赖
  if [[ -f "$REQ_FILE" ]]; then
    export PIP_NO_COMPILE=1
    export PYTHONDONTWRITEBYTECODE=1
    pip install --no-compile -r "$REQ_FILE"
  fi
  # 安装打包工具
  pip install --no-compile "pyinstaller>=6.9,<7" "altgraph>=0.17.4" "macholib>=1.16.3"
}

build_one_arch() {
  local arch="$1"  # arm64 | x86_64
  echo "==> 构建架构：$arch"

  # Apple Silicon 构建 x86_64 需要 Rosetta
  if [[ "$arch" == "x86_64" ]]; then
    ensure_rosetta_for_x86
  fi

  install_tools_and_deps "$arch"

  local venv="$(venv_for_arch "$arch")"
  # shellcheck disable=SC1090
  source "$venv/bin/activate"

  local VERSION_SUFFIX=""
  if [[ -f "$VERSION_FILE" ]]; then
    local VERSION
    VERSION="$(python - <<'PY'
from version import version
print(version)
PY
)"
    if [[ -n "$VERSION" ]]; then
      VERSION_SUFFIX="-v${VERSION}"
    fi
  fi

  local NAME="${APP_NAME}${VERSION_SUFFIX}-${arch}"
  local PYI_ARGS=(
    --noconfirm
    --name "$NAME"
    --windowed
  )
  if [[ -f "$ICON_FILE" ]]; then
    PYI_ARGS+=( --icon "$ICON_FILE" )
  fi

  # 这里可按需添加数据文件，例如：
  # PYI_ARGS+=( --add-data "assets:assets" )

  pyinstaller "${PYI_ARGS[@]}" "$ENTRY_FILE"

  local APP_PATH="dist/${NAME}.app"
  [[ -d "$APP_PATH" ]] || { echo "打包失败：$APP_PATH 不存在"; exit 1; }
  echo "✅ 产物：$APP_PATH"

  # 可选：生成 DMG
  local DMG_PATH="dist/${NAME}.dmg"
  hdiutil create -volname "$NAME" -srcfolder "$APP_PATH" -ov -format UDZO "$DMG_PATH"
  echo "✅ DMG：$DMG_PATH"

  deactivate || true
}

usage() {
  cat <<EOF
用法：
  $0 mac-arm64       仅打 arm64.app
  $0 mac-x86_64      仅打 x86_64.app
  $0 mac-all         两个都打
EOF
}

# ================== 主流程 ==================
target="${1:-mac-all}"
case "$target" in
  mac-arm64)   build_one_arch arm64 ;;
  mac-x86_64)  build_one_arch x86_64 ;;
  mac-all)     build_one_arch arm64; build_one_arch x86_64 ;;
  *)           usage; exit 2 ;;
esac

echo "🎉 Done."
