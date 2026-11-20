param([int]$Port = 7873)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Write-Host "知序工作台（PDF→MD→教案→PPT）: http://127.0.0.1:$Port/"
python run-local.py --port $Port