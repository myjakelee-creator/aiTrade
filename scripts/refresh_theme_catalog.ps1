param(
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$Python32 = "C:\Users\myjay\AppData\Local\Programs\Python\Python310-32\python.exe"
$CollectorPidFile = Join-Path $RuntimeDir "collector32.pid"
$StatusFile = Join-Path $RuntimeDir "theme_catalog_refresh_status.json"
$CatalogFile = Join-Path $RuntimeDir "theme_membership.json"

Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath $Python32)) {
    throw "32-bit Python not found: $Python32"
}

$bits = & $Python32 -c "import struct; print(struct.calcsize('P') * 8)"
if ([string]$bits -ne "32") {
    throw "Theme catalog refresh requires 32-bit Python: $Python32"
}

$collectorPid = 0
if (Test-Path -LiteralPath $CollectorPidFile) {
    $raw = Get-Content -LiteralPath $CollectorPidFile -ErrorAction SilentlyContinue |
        Select-Object -First 1
    [void][int]::TryParse([string]$raw, [ref]$collectorPid)
}
if ($collectorPid -gt 0 -and (Get-Process -Id $collectorPid -ErrorAction SilentlyContinue)) {
    throw "StockBoard collector PID $collectorPid is running. Run '.\stockboard_v2_large.cmd stop' first."
}

Write-Host ""
Write-Host "== Refreshing Kiwoom theme catalog once ==" -ForegroundColor Cyan
Write-Host "PROJECT_ROOT=$ProjectRoot"
Write-Host "PYTHON32=$Python32"
Write-Host "POLICY=offline_once_no_realtime_collector_work"

& $Python32 -m realtime_v2.theme_catalog_refresh32 --timeout-sec $TimeoutSec
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    if (Test-Path -LiteralPath $StatusFile) {
        Write-Host "---- refresh status ----" -ForegroundColor Yellow
        Get-Content -LiteralPath $StatusFile -Raw
    }
    throw "Theme catalog refresh failed with exit code $exitCode"
}

if (-not (Test-Path -LiteralPath $CatalogFile)) {
    throw "Theme catalog file was not created: $CatalogFile"
}

$status = Get-Content -LiteralPath $StatusFile -Raw | ConvertFrom-Json
$status |
    Select-Object status,theme_count,membership_count,master_version,generated_at,output_path,last_error |
    Format-List

Write-Host "THEME_CATALOG_REFRESH_OK=True" -ForegroundColor Green
Write-Host "Restart StockBoard to load the refreshed runtime catalog."
