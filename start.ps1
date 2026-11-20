param([int]$Port = 7863)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python run-local.py --port $Port
