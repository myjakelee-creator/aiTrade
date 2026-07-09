@echo off
setlocal EnableExtensions
cd /d C:\aiTrade

set "ACTION=%~1"
if "%ACTION%"=="" goto menu
goto run

:menu
echo.
echo StockBoard v2 Realtime - Large Trade Aggregation Test
echo.
echo   1 Start large normal
echo   2 Stop v2
echo   3 Restart large normal
echo   4 Status large
echo   5 Start large fast-open
echo   6 Restart large fast-open
echo   0 Exit
echo.
set /p "CHOICE=Select: "
if "%CHOICE%"=="1" set "ACTION=start"
if "%CHOICE%"=="2" set "ACTION=stop"
if "%CHOICE%"=="3" set "ACTION=restart"
if "%CHOICE%"=="4" set "ACTION=status"
if "%CHOICE%"=="5" set "ACTION=start-fast"
if "%CHOICE%"=="6" set "ACTION=restart-fast"
if "%CHOICE%"=="0" exit /b 0
if "%ACTION%"=="" (
  echo Invalid selection.
  goto menu
)

:run
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$raw=Get-Content -LiteralPath '%~f0' -Raw; $marker='# POWERSHELL-BEGIN'; $idx=$raw.LastIndexOf($marker); if($idx -lt 0){throw 'PowerShell marker not found'}; $env:STOCKBOARD_V2_LARGE_ACTION='%ACTION%'; Invoke-Expression $raw.Substring($idx + $marker.Length)"
if errorlevel 1 (
  echo.
  echo StockBoard v2 large command finished with an error or warning. Review messages above.
  pause
)
exit /b %ERRORLEVEL%

# POWERSHELL-BEGIN
$ErrorActionPreference = "Stop"

$Action = [string]$env:STOCKBOARD_V2_LARGE_ACTION
$Action = $Action.Trim().ToLowerInvariant()
$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$Python32 = "C:\Users\myjay\AppData\Local\Programs\Python\Python310-32\python.exe"
$Python64 = (Get-Command python -ErrorAction Stop).Source
$WorkerPidFile = Join-Path $RuntimeDir "worker64.pid"
$CollectorPidFile = Join-Path $RuntimeDir "collector32.pid"
$ContextPidFile = Join-Path $RuntimeDir "context_snapshot_writer.pid"
$WorkerUrl = "http://127.0.0.1:8765/api/v2/health"
$BoardUrl = "http://127.0.0.1:8765/"
$OldLauncher = Join-Path $ProjectRoot "stockboard_v2_live.cmd"

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
    if (-not (Test-Path -LiteralPath $PidFile)) { return }
    $raw = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $pidNumber = 0
    if ([int]::TryParse([string]$raw, [ref]$pidNumber)) {
        $proc = Get-Process -Id $pidNumber -ErrorAction SilentlyContinue
        if ($null -ne $proc) {
            Write-Host "Stopping $Name PID=$pidNumber"
            Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Stop-Port([int]$Port) {
    try {
        $pids = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique)
    } catch { $pids = @() }
    foreach ($pidNumber in $pids) {
        if ($pidNumber -gt 0) {
            Write-Host "Stopping port $Port listener PID=$pidNumber"
            Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
        }
    }
}

function Stop-LargeProcesses {
    Ensure-RuntimeDir
    Stop-PidFile $CollectorPidFile "collector32"
    Stop-PidFile $WorkerPidFile "worker64"
    Stop-PidFile $ContextPidFile "context_snapshot_writer"
    try {
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.CommandLine -and ($_.CommandLine -like "*realtime_v2\collector32_large.py*" -or $_.CommandLine -like "*realtime_v2\worker64_guarded_large.py*") } |
            ForEach-Object {
                Write-Host "Stopping large wrapper PID=$($_.ProcessId)"
                Stop-Process -Id ([int]$_.ProcessId) -Force -ErrorAction SilentlyContinue
            }
    } catch { }
    Stop-Port 8765
    Stop-Port 8710
}

function Stop-V2 {
    Write-Step "Stopping old v2 processes"
    if (Test-Path -LiteralPath $OldLauncher) {
        & cmd.exe /c "`"$OldLauncher`" stop"
    }
    Stop-LargeProcesses
}

function Test-WorkerReady {
    try {
        Invoke-RestMethod -Uri $WorkerUrl -TimeoutSec 1 | Out-Null
        return $true
    } catch { return $false }
}

function Wait-WorkerReady([int]$TimeoutSec = 10) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-WorkerReady) { return $true }
        Start-Sleep -Milliseconds 300
    }
    return $false
}

function Wait-CollectorOpenApiReady([int]$TimeoutSec = 75) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $snapshot = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/v2/snapshot?limit=1" -TimeoutSec 1
            $collectorStatus = $snapshot.status.collector_status
            if ($collectorStatus -and $collectorStatus.provider_started -eq $true -and [int]($collectorStatus.registered_count) -gt 0) {
                Write-Host "COLLECTOR_OPENAPI_READY=True registered_count=$($collectorStatus.registered_count)"
                return $true
            }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "COLLECTOR_OPENAPI_READY=False timeout=${TimeoutSec}s; continuing startup" -ForegroundColor Yellow
    return $false
}

function Start-V2Large([bool]$FastOpen = $false) {
    Ensure-RuntimeDir
    Stop-V2

    Write-Step "Building v2 universe"
    & $Python64 (Join-Path $ProjectRoot "realtime_v2\build_universe.py") --limit 300 --rank-basis today
    if ($LASTEXITCODE -ne 0) { throw "build_universe failed: $LASTEXITCODE" }

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $workerOut = Join-Path $RuntimeDir "worker64_large_$stamp.out.log"
    $workerErr = Join-Path $RuntimeDir "worker64_large_$stamp.err.log"
    $collectorOut = Join-Path $RuntimeDir "collector32_large_$stamp.out.log"
    $collectorErr = Join-Path $RuntimeDir "collector32_large_$stamp.err.log"
    $contextOut = Join-Path $RuntimeDir "context_snapshot_$stamp.out.log"
    $contextErr = Join-Path $RuntimeDir "context_snapshot_$stamp.err.log"

    Write-Step "Starting low-priority context snapshot writer"
    $contextArgs = @("realtime_v2\context_snapshot_writer.py", "--interval-sec", "30", "--ohlc-bootstrap")
    $context = Start-Process -FilePath $Python64 -ArgumentList $contextArgs -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $contextOut -RedirectStandardError $contextErr -PassThru
    Set-Content -LiteralPath $ContextPidFile -Value $context.Id -Encoding ASCII
    Write-Host "CONTEXT_PID=$($context.Id)"

    Write-Step "Starting 64-bit guarded worker with large-trade delta support"
    $worker = Start-Process -FilePath $Python64 -ArgumentList @("realtime_v2\worker64_guarded_large.py") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $workerOut -RedirectStandardError $workerErr -PassThru
    Set-Content -LiteralPath $WorkerPidFile -Value $worker.Id -Encoding ASCII
    Write-Host "WORKER64_PID=$($worker.Id)"
    Write-Host "WORKER64_STDOUT=$workerOut"
    Write-Host "WORKER64_STDERR=$workerErr"

    if (-not (Wait-WorkerReady 10)) {
        Write-Warning "worker did not respond yet; collector will still be started"
    }

    Write-Step "Starting 32-bit collector with pre-coalescing large-trade aggregation"
    if (-not (Test-Path -LiteralPath $Python32)) { throw "32-bit Python not found: $Python32" }
    $collectorArgs = @("realtime_v2\collector32_large.py", "--limit", "300", "--suffix", "AL", "--flush-ms", "50")
    if ($FastOpen) {
        Write-Host "FAST_OPEN=True"
        Write-Host "ORDERBOOK=False during fast-open mode"
    } else {
        $collectorArgs += "--orderbook"
        Write-Host "FAST_OPEN=False"
        Write-Host "ORDERBOOK=True"
    }
    $oldHideCollectorConsole = $env:STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN
    $env:STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN = "1"
    try {
        $collector = Start-Process -FilePath $Python32 -ArgumentList $collectorArgs -WorkingDirectory $ProjectRoot -WindowStyle Normal -RedirectStandardOutput $collectorOut -RedirectStandardError $collectorErr -PassThru
    } finally {
        if ($null -eq $oldHideCollectorConsole) {
            Remove-Item Env:\STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN -ErrorAction SilentlyContinue
        } else {
            $env:STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN = $oldHideCollectorConsole
        }
    }
    Set-Content -LiteralPath $CollectorPidFile -Value $collector.Id -Encoding ASCII
    Write-Host "COLLECTOR32_PID=$($collector.Id)"
    Write-Host "COLLECTOR32_STDOUT=$collectorOut"
    Write-Host "COLLECTOR32_STDERR=$collectorErr"

    Write-Step "Waiting for OpenAPI login and registration"
    [void](Wait-CollectorOpenApiReady 75)

    Write-Step "Starting HTS bridge"
    if (Test-Path -LiteralPath $OldLauncher) {
        & cmd.exe /c "`"$OldLauncher`" ahk"
    }

    Start-Process $BoardUrl
    Write-Host ""
    Write-Host "Open $BoardUrl"
}

function Status-V2Large {
    Ensure-RuntimeDir
    try {
        $snapshot = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/v2/snapshot?limit=5" -TimeoutSec 1
        Write-Host "WORKER_HEALTH=True"
        Write-Host "ROW_COUNT=$($snapshot.row_count)"
        Write-Host "EVENT_COUNT=$($snapshot.status.event_count)"
        Write-Host "TRADE_COUNT=$($snapshot.status.trade_count)"
        $sender = $snapshot.status.collector_status.sender_stats
        if ($sender) {
            Write-Host "COLLECTOR_CONNECTED=$($sender.connected)"
            Write-Host "COLLECTOR_PENDING_TOTAL=$($sender.pending_total_count)"
            Write-Host "COLLECTOR_SENT_PER_SEC=$($sender.sent_per_sec)"
            Write-Host "COLLECTOR_LARGE_BUY_COUNT=$($sender.large_trade_buy_count)"
            Write-Host "COLLECTOR_LARGE_SELL_COUNT=$($sender.large_trade_sell_count)"
            Write-Host "COLLECTOR_LARGE_BUY_SUM_EOK=$($sender.large_trade_buy_sum_eok)"
            Write-Host "COLLECTOR_LARGE_SELL_SUM_EOK=$($sender.large_trade_sell_sum_eok)"
        }
    } catch {
        Write-Host "WORKER_HEALTH=False"
        Write-Host "ERROR=$($_.Exception.Message)"
    }
}

if ($Action -eq "start") { Start-V2Large $false; exit 0 }
if ($Action -eq "start-fast") { Start-V2Large $true; exit 0 }
if ($Action -eq "restart") { Start-V2Large $false; exit 0 }
if ($Action -eq "restart-fast") { Start-V2Large $true; exit 0 }
if ($Action -eq "stop") { Stop-V2; exit 0 }
if ($Action -eq "status") { Status-V2Large; exit 0 }
throw "unknown action: $Action"
