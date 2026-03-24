$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "== Build frontend ==" -ForegroundColor Cyan
Push-Location (Join-Path $root 'frontend')
if (-not (Test-Path 'node_modules')) {
  npm install
}
npm run build
Pop-Location

Write-Host "== Copy dist -> backend/static ==" -ForegroundColor Cyan
$staticDir = Join-Path $root 'backend\static'
$distDir = Join-Path $root 'frontend\dist'

if (-not (Test-Path $distDir)) {
  throw "frontend dist not found: $distDir"
}

if (Test-Path $staticDir) {
  Remove-Item $staticDir -Recurse -Force
}
New-Item -ItemType Directory -Path $staticDir | Out-Null
Copy-Item (Join-Path $distDir '*') -Destination $staticDir -Recurse -Force

Write-Host "== Start backend (serving frontend) ==" -ForegroundColor Cyan
Push-Location (Join-Path $root 'backend')
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

uvicorn app.main:app --host 0.0.0.0 --port 8000
Pop-Location
