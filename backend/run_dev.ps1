$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

python -m venv .venv
. .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
