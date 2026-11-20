# 一键安装（Windows）：检查环境 -> 建虚拟环境 -> 安装依赖 -> 自检
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "../..")
Set-Location $root

$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) { throw "未找到 Python。请安装 Python 3.11（勾选 Add to PATH）。" }

$version = & py -3.11 -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
if ($LASTEXITCODE -ne 0 -or $version -ne "3.11") {
    Write-Host "未检测到 Python 3.11（当前：$version）。依赖按 3.11 锁定，建议先安装 3.11。" -ForegroundColor Yellow
}

if (-not (Test-Path ".venv")) { py -3.11 -m venv .venv }
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "已生成 .env：请填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / LLM_OUTLINE_JSON_MODEL。" -ForegroundColor Yellow
}
if (-not (Test-Path "local-config.json") -and (Test-Path "local-config.example.json")) {
    Copy-Item "local-config.example.json" "local-config.json"
    Write-Host "已生成 local-config.json：请填写 PPTMASTER_SKILL_DIR 与 PPTNATIVE_RUNTIME_HOST。" -ForegroundColor Yellow
}

& ".venv\Scripts\python.exe" scripts/doctor.py
Write-Host ""
Write-Host "安装完成。启动：.venv\Scripts\python.exe run-local.py --port 7873" -ForegroundColor Green
