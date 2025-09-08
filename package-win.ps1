Param(
  [string]$Name = "FindDiffEditor",
  [string]$IconPath = "pyside_app\i.ico"
)

$ErrorActionPreference = "Stop"

# Go to repo root
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# 1) Ensure venv
if (-not (Test-Path ".venv")) {
  py -3 -m venv .venv
}
. ".venv\Scripts\Activate.ps1"

# 2) Tools + deps
python -m pip install --upgrade pip setuptools wheel
pip install -r pyside_app\requirements.txt
pip install "pyinstaller>=6.9,<7"

# 3) Build
$pyiArgs = @(
  "--noconfirm",
  "--name", $Name,
  "--windowed",
  "--add-data", "pyside_app\banana.py;pyside_app",
  "--hidden-import", "PIL",
  "--hidden-import", "requests"
)
if (Test-Path $IconPath) {
  $pyiArgs += @("--icon", $IconPath)
}

pyinstaller @pyiArgs pyside_app\main.py

$OutDir = Join-Path "dist" $Name
if (-not (Test-Path $OutDir)) {
  Write-Error "Build failed: $OutDir not found"
}

Write-Host "Built: $OutDir\$Name.exe"
