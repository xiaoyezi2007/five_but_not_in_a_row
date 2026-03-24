$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

npm install
npm run dev -- --host
