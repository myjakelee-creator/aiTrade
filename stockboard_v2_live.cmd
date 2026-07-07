@echo off
setlocal EnableExtensions
cd /d C:\aiTrade

set "ACTION=%~1"
if "%ACTION%"=="" goto menu
goto run

:menu
echo.
echo StockBoard v2 Realtime
echo.
echo   1 Start v2
echo   2 Stop v2
echo   3 Restart v2
echo   4 Status v2
echo   0 Exit
echo.
set /p "CHOICE=Select: "
if "%CHOICE%"=="1" set "ACTION=start"
if "%CHOICE%"=="2" set "ACTION=stop"
if "%CHOICE%"=="3" set "ACTION=restart"
if "%CHOICE%"=="4" set "ACTION=status"
if "%CHOICE%"=="0" exit /b 0
if "%ACTION%"=="" (
  echo Invalid selection.
  goto menu
)

:run
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$raw=Get-Content -LiteralPath '%~f0' -Raw; $marker='# POWERSHELL-BEGIN'; $idx=$raw.LastIndexOf($marker); if($idx -lt 0){throw 'PowerShell marker not found'}; $env:STOCKBOARD_V2_ACTION='%ACTION%'; Invoke-Expression $raw.Substring($idx + $marker.Length)"
if errorlevel 1 (
  echo.
  echo StockBoard v2 command finished with an error or warning. Review the messages above.
  pause
)
exit /b %ERRORLEVEL%

# POWERSHELL-BEGIN
$ErrorActionPreference = "Stop"

$Action = [string]$env:STOCKBOARD_V2_ACTION
$Action = $Action.Trim().ToLowerInvariant()
$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$Python32 = "C:\Users\myjay\AppData\Local\Programs\Python\Python310-32\python.exe"
$Python64 = (Get-Command python -ErrorAction Stop).Source
$WorkerPidFile = Join-Path $RuntimeDir "worker64.pid"
$CollectorPidFile = Join-Path $RuntimeDir "collector32.pid"
$WorkerUrl = "http://127.0.0.1:8765/api/v2/health"
$BoardUrl = "http://127.0.0.1:8765/"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Ensure-RuntimeDir {
    if (-not (Test-Path -LiteralPath $RuntimeDir)) {
        New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    }
}

function Stop-PidFile([string]$PidFile, [string]$Name) {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        Write-Host "$Name pid file not found."
        return
    }
    $raw = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $pidNumber = 0
    if (-not [int]::TryParse([string]$raw, [ref]$pidNumber)) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        return
    }
    $proc = Get-Process -Id $pidNumber -ErrorAction SilentlyContinue
    if ($null -ne $proc) {
        Write-Host "Stopping $Name PID=$pidNumber"
        Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "$Name PID=$pidNumber is not running."
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Stop-Port([int]$Port) {
    try {
        $pids = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        $pids = @()
    }
    foreach ($pidNumber in $pids) {
        if ($pidNumber -gt 0) {
            Write-Host "Stopping port $Port listener PID=$pidNumber"
            Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
        }
    }
}

function Test-WorkerReady {
    try {
        Invoke-RestMethod -Uri $WorkerUrl -TimeoutSec 1 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Start-V2 {
    Ensure-RuntimeDir
    Write-Step "Stopping old v2 processes"
    Stop-PidFile $CollectorPidFile "collector32"
    Stop-PidFile $WorkerPidFile "worker64"
    Stop-Port 8765
    Stop-Port 8710

    Write-Step "Building v2 universe"
    & $Python64 (Join-Path $ProjectRoot "realtime_v2\build_universe.py") --limit 300 --rank-basis today
    if ($LASTEXITCODE -ne 0) { throw "build_universe failed: $LASTEXITCODE" }

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $workerOut = Join-Path $RuntimeDir "worker64_$stamp.out.log"
    $workerErr = Join-Path $RuntimeDir "worker64_$stamp.err.log"
    $collectorOut = Join-Path $RuntimeDir "collector32_$stamp.out.log"
    $collectorErr = Join-Path $RuntimeDir "collector32_$stamp.err.log"

    Write-Step "Starting 64-bit guarded worker"
    $worker = Start-Process -FilePath $Python64 -ArgumentList @("realtime_v2\worker64_guarded.py") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $workerOut -RedirectStandardError $workerErr -PassThru
    Set-Content -LiteralPath $WorkerPidFile -Value $worker.Id -Encoding ASCII
    Write-Host "WORKER64_PID=$($worker.Id)"
    Write-Host "WORKER64_STDOUT=$workerOut"
    Write-Host "WORKER64_STDERR=$workerErr"

    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        if (Test-WorkerReady) { break }
        Start-Sleep -Milliseconds 300
    }
    if (-not (Test-WorkerReady)) {
        Write-Warning "worker did not respond yet; collector will still be started"
    }

    Write-Step "Starting 32-bit collector"
    if (-not (Test-Path -LiteralPath $Python32)) {
        throw "32-bit Python not found: $Python32"
    }
    $collector = Start-Process -FilePath $Python32 -ArgumentList @("realtime_v2\collector32.py", "--limit", "300", "--suffix", "AL", "--orderbook") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $collectorOut -RedirectStandardError $collectorErr -PassThru
    Set-Content -LiteralPath $CollectorPidFile -Value $collector.Id -Encoding ASCII
    Write-Host "COLLECTOR32_PID=$($collector.Id)"
    Write-Host "COLLECTOR32_STDOUT=$collectorOut"
    Write-Host "COLLECTOR32_STDERR=$collectorErr"

    Start-Process $BoardUrl
    Write-Host ""
    Write-Host "Open $BoardUrl"
}

function Stop-V2 {
    Ensure-RuntimeDir
    Write-Step "Stopping v2 collector/worker"
    Stop-PidFile $CollectorPidFile "collector32"
    Stop-PidFile $WorkerPidFile "worker64"
    Stop-Port 8765
    Stop-Port 8710
}

function Status-V2 {
    Ensure-RuntimeDir
    Write-Host "WORKER_PID_FILE=$WorkerPidFile"
    if (Test-Path -LiteralPath $WorkerPidFile) { Write-Host "WORKER_PID=$(Get-Content -LiteralPath $WorkerPidFile | Select-Object -First 1)" }
    Write-Host "COLLECTOR_PID_FILE=$CollectorPidFile"
    if (Test-Path -LiteralPath $CollectorPidFile) { Write-Host "COLLECTOR_PID=$(Get-Content -LiteralPath $CollectorPidFile | Select-Object -First 1)" }
    try {
        $health = Invoke-RestMethod -Uri $WorkerUrl -TimeoutSec 1
        Write-Host "WORKER_HEALTH=True"
        $snapshot = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/v2/snapshot?limit=5" -TimeoutSec 1
        Write-Host "ROW_COUNT=$($snapshot.row_count)"
        Write-Host "EVENT_COUNT=$($snapshot.status.event_count)"
        Write-Host "TRADE_COUNT=$($snapshot.status.trade_count)"
        Write-Host "ORDERBOOK_COUNT=$($snapshot.status.orderbook_count)"
        Write-Host "DROPPED_TRADE_COUNT=$($snapshot.status.dropped_trade_count)"
        Write-Host "LAST_DROPPED_TRADE=$($snapshot.status.last_dropped_trade | ConvertTo-Json -Compress)"
        Write-Host "LAST_EVENT_AT=$($snapshot.status.last_event_at)"
    } catch {
        Write-Host "WORKER_HEALTH=False"
        Write-Host "ERROR=$($_.Exception.Message)"
    }
}

if ($Action -eq "start") { Start-V2; exit 0 }
if ($Action -eq "stop") { Stop-V2; exit 0 }
if ($Action -eq "restart") { Stop-V2; Start-V2; exit 0 }
if ($Action -eq "status") { Status-V2; exit 0 }
throw "unknown action: $Action"
