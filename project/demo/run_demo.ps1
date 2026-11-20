# 一键启动演示（Windows + 桌面 PowerPoint）
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Resolve-Path (Join-Path $here "../..")
$env:PPTNATIVE_DATA_DIR = Join-Path $here "data"
$env:AUTH_SECRET = "demo-only-secret-change-me"
$env:AUTH_CLAIM_LEGACY = "0"
Set-Location $root

if (-not $env:PPTMASTER_SKILL_DIR) {
    if (Test-Path (Join-Path $root "runtime/ppt-master/SKILL.md")) {
        $env:PPTMASTER_SKILL_DIR = Join-Path $root "runtime/ppt-master"
    } else {
        Write-Host "缺少 PPT Master 运行时：请设置 PPTMASTER_SKILL_DIR，或放到 runtime/ppt-master" -ForegroundColor Yellow
    }
}
if (-not (Test-Path ".env") -and -not $env:PPTNATIVE_ENV_FILE) {
    Write-Host "未发现 .env：复制 .env.example 为 .env 并填写 LLM_BASE_URL / LLM_API_KEY 后再演示生成。" -ForegroundColor Yellow
}

python (Join-Path $here "seed_demo.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "演示地址：http://127.0.0.1:7873/" -ForegroundColor Green
Write-Host "演示账号：demo / Demo12345!" -ForegroundColor Green
Write-Host "停止：Ctrl+C" -ForegroundColor DarkGray
python run-local.py --port 7873
