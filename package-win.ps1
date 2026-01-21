Param(
  [string]$Name = "FindCatsTool",
  [string]$IconPath = "pyside_app\i.ico"
)

$ErrorActionPreference = "Stop"

# 到仓库根目录
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# 1) venv
if (-not (Test-Path ".venv")) {
  py -3 -m venv .venv
}
. ".venv\Scripts\Activate.ps1"

# 2) 依赖（只装 PySide6）
python -m pip install --upgrade pip setuptools wheel
if (Test-Path "pyside_app\requirements.txt") {
  pip install -r pyside_app\requirements.txt
} elseif (Test-Path "requirements.txt") {
  pip install -r requirements.txt
} else {
  pip install pyside6
}
pip install "pyinstaller>=6.9,<7" "altgraph>=0.17.4"

# 3) 入口与图标
$Entry = "pyside_app\main.py"
if (-not (Test-Path $Entry)) {
  if (Test-Path "main.py") { $Entry = "main.py" }
  else { Write-Error "未找到入口文件：pyside_app\main.py 或 main.py" }
}

# 4) PyInstaller 参数（仅 PySide6，补齐常见隐藏依赖）
$pyiArgs = @(
  "--noconfirm",
  "--clean",
  "--name", $Name,
  "--windowed"
)
if (Test-Path $IconPath) {
  $pyiArgs += @("--icon", $IconPath)
}

# 5) 打包（保持 onedir，和你原来一致；如需单文件可加 --onefile）
pyinstaller @pyiArgs $Entry

# 6) 结果检查
$OutDir = Join-Path "dist" $Name
if (-not (Test-Path $OutDir)) {
  Write-Error "Build failed: $OutDir not found"
}
Write-Host "Built: $OutDir\$Name.exe"

deactivate | Out-Null
