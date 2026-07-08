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
echo   1 Start v2 normal
echo   2 Stop v2
echo   3 Restart v2 normal
echo   4 Status v2
echo   5 Start HTS Bridge only
echo   6 Start v2 fast-open ^(trade only, no orderbook^)
echo   7 Restart v2 fast-open
echo   0 Exit
echo.
set /p "CHOICE=Select: "
if "%CHOICE%"=="1" set "ACTION=start"
if "%CHOICE%"=="2" set "ACTION=stop"
if "%CHOICE%"=="3" set "ACTION=restart"
if "%CHOICE%"=="4" set "ACTION=status"
if "%CHOICE%"=="5" set "ACTION=ahk"
if "%CHOICE%"=="6" set "ACTION=start-fast"
if "%CHOICE%"=="7" set "ACTION=restart-fast"
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
$ContextPidFile = Join-Path $RuntimeDir "context_snapshot_writer.pid"
$WorkerUrl = "http://127.0.0.1:8765/api/v2/health"
$BoardUrl = "http://127.0.0.1:8765/"
$AhkScript = Join-Path $ProjectRoot "scripts\stockboard_kiwoom_link_v1.ahk"
$AhkPidFile = Join-Path $RuntimeDir "stockboard_v2_ahk.pid"
$AhkStatusFile = Join-Path $RuntimeDir "hts_link_status.txt"
$AhkExeCandidates = @(
    "C:\Program Files\AutoHotkey\v1.1.37.02\AutoHotkeyU64.exe",
    "C:\Program Files\AutoHotkey\v1.1.37.02\AutoHotkeyU32.exe",
    "C:\Program Files\AutoHotkey\v1.1.37.02\AutoHotkeyA32.exe",
    "C:\Program Files\AutoHotkey\v1.1\AutoHotkeyU64.exe",
    "C:\Program Files\AutoHotkey\v1.1\AutoHotkeyU32.exe",
    "C:\Program Files\AutoHotkey\AutoHotkey.exe",
    "C:\Program Files (x86)\AutoHotkey\AutoHotkey.exe"
)

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

function Resolve-AhkExe {
    foreach ($candidate in $AhkExeCandidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Stop-HtsBridge {
    Ensure-RuntimeDir
    $scriptName = Split-Path -Leaf $AhkScript
    $ids = New-Object System.Collections.Generic.HashSet[int]
    try {
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.Name -like "AutoHotkey*" -and $_.CommandLine -and $_.CommandLine -like "*$scriptName*" } |
            ForEach-Object { [void]$ids.Add([int]$_.ProcessId) }
    } catch {
        Write-Host "AHK bridge lookup warning: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    if (Test-Path -LiteralPath $AhkPidFile) {
        $raw = Get-Content -LiteralPath $AhkPidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        $pidNumber = 0
        if ([int]::TryParse([string]$raw, [ref]$pidNumber)) {
            [void]$ids.Add($pidNumber)
        }
    }
    foreach ($pidNumber in $ids) {
        try {
            Write-Host "Stopping HTS bridge PID=$pidNumber"
            Stop-Process -Id $pidNumber -Force -ErrorAction Stop
        } catch {
            Write-Host "Could not stop HTS bridge PID=${pidNumber}: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    Remove-Item -LiteralPath $AhkPidFile -Force -ErrorAction SilentlyContinue
}

function Start-HtsBridge {
    Ensure-RuntimeDir
    if (-not (Test-Path -LiteralPath $AhkScript)) {
        Write-Host "AHK bridge script not found: $AhkScript" -ForegroundColor Yellow
        return $false
    }
    $ahkExe = Resolve-AhkExe
    if (-not $ahkExe) {
        Write-Host "AutoHotkey v1 executable not found." -ForegroundColor Yellow
        return $false
    }
    Stop-HtsBridge
    try {
        Write-Host "Starting HTS bridge as administrator. Approve the UAC prompt if Windows asks."
        $process = Start-Process -FilePath $ahkExe -ArgumentList "`"$AhkScript`"" -Verb RunAs -PassThru -ErrorAction Stop
        if ($process -and $process.Id) {
            Set-Content -LiteralPath $AhkPidFile -Value ([string]$process.Id) -Encoding ASCII
            Write-Host "AHK_PID=$($process.Id)"
        }
        Write-Host "AHK_EXE=$ahkExe"
        Write-Host "AHK_SCRIPT=$AhkScript"
        return $true
    } catch {
        Write-Host "HTS bridge start failed: $($_.Exception.Message)" -ForegroundColor Yellow
        return $false
    }
}

function Get-HtsBridgeStatus {
    $scriptName = Split-Path -Leaf $AhkScript
    $pids = @()
    try {
        $pids = @(Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.Name -like "AutoHotkey*" -and $_.CommandLine -and $_.CommandLine -like "*$scriptName*" } |
            Select-Object -ExpandProperty ProcessId -Unique)
    } catch { $pids = @() }
    $lastStatus = ""
    if (Test-Path -LiteralPath $AhkStatusFile) {
        $lastStatus = Get-Content -LiteralPath $AhkStatusFile -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    return @{ running = ($pids.Count -gt 0); pids = $pids; last_status = $lastStatus }
}

function Test-WorkerReady {
    try {
        Invoke-RestMethod -Uri $WorkerUrl -TimeoutSec 1 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Start-V2([bool]$FastOpen = $false) {
    Ensure-RuntimeDir
    Write-Step "Stopping old v2 processes"
    Stop-PidFile $CollectorPidFile "collector32"
    Stop-PidFile $WorkerPidFile "worker64"
    Stop-PidFile $ContextPidFile "context_snapshot_writer"
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
    $contextOut = Join-Path $RuntimeDir "context_snapshot_$stamp.out.log"
    $contextErr = Join-Path $RuntimeDir "context_snapshot_$stamp.err.log"

    Write-Step "Starting low-priority context snapshot writer"
    $contextArgs = @("realtime_v2\context_snapshot_writer.py", "--interval-sec", "30", "--ohlc-bootstrap")
    $context = Start-Process -FilePath $Python64 -ArgumentList $contextArgs -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $contextOut -RedirectStandardError $contextErr -PassThru
    Set-Content -LiteralPath $ContextPidFile -Value $context.Id -Encoding ASCII
    Write-Host "CONTEXT_PID=$($context.Id)"
    Write-Host "CONTEXT_STDOUT=$contextOut"
    Write-Host "CONTEXT_STDERR=$contextErr"

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
    $collectorArgs = @("realtime_v2\collector32.py", "--limit", "300", "--suffix", "AL", "--flush-ms", "50")
    if ($FastOpen) {
        Write-Host "FAST_OPEN=True"
        Write-Host "ORDERBOOK=False during fast-open mode"
    } else {
        $collectorArgs += "--orderbook"
        Write-Host "FAST_OPEN=False"
        Write-Host "ORDERBOOK=True"
    }
    $env:STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN = "1"
    $collector = Start-Process -FilePath $Python32 -ArgumentList $collectorArgs -WorkingDirectory $ProjectRoot -WindowStyle Normal -RedirectStandardOutput $collectorOut -RedirectStandardError $collectorErr -PassThru
    Set-Content -LiteralPath $CollectorPidFile -Value $collector.Id -Encoding ASCII
    Write-Host "COLLECTOR32_PID=$($collector.Id)"
    Write-Host "COLLECTOR32_STDOUT=$collectorOut"
    Write-Host "COLLECTOR32_STDERR=$collectorErr"

    Write-Step "Starting HTS bridge"
    [void](Start-HtsBridge)

    Start-Process $BoardUrl
    Write-Host ""
    Write-Host "Open $BoardUrl"
}

function Stop-V2 {
    Ensure-RuntimeDir
    Write-Step "Stopping v2 collector/worker/context/HTS bridge"
    Stop-HtsBridge
    Stop-PidFile $CollectorPidFile "collector32"
    Stop-PidFile $WorkerPidFile "worker64"
    Stop-PidFile $ContextPidFile "context_snapshot_writer"
    Stop-Port 8765
    Stop-Port 8710
}

function Status-V2 {
    Ensure-RuntimeDir
    Write-Host "WORKER_PID_FILE=$WorkerPidFile"
    if (Test-Path -LiteralPath $WorkerPidFile) { Write-Host "WORKER_PID=$(Get-Content -LiteralPath $WorkerPidFile | Select-Object -First 1)" }
    Write-Host "COLLECTOR_PID_FILE=$CollectorPidFile"
    if (Test-Path -LiteralPath $CollectorPidFile) { Write-Host "COLLECTOR_PID=$(Get-Content -LiteralPath $CollectorPidFile | Select-Object -First 1)" }
    Write-Host "CONTEXT_PID_FILE=$ContextPidFile"
    if (Test-Path -LiteralPath $ContextPidFile) { Write-Host "CONTEXT_PID=$(Get-Content -LiteralPath $ContextPidFile | Select-Object -First 1)" }
    $contextStatus = Join-Path $RuntimeDir "context_snapshot_status.json"
    if (Test-Path -LiteralPath $contextStatus) { Write-Host "CONTEXT_STATUS=$(Get-Content -LiteralPath $contextStatus -Raw)" }
    $hts = Get-HtsBridgeStatus
    Write-Host "AHK_RUNNING=$($hts.running)"
    Write-Host "AHK_PIDS=$($hts.pids -join ',')"
    Write-Host "AHK_LAST_STATUS=$($hts.last_status)"
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
        $collectorStatus = $snapshot.status.collector_status
        if ($collectorStatus -and $collectorStatus.sender_stats) {
            $sender = $collectorStatus.sender_stats
            Write-Host "COLLECTOR_CONNECTED=$($sender.connected)"
            Write-Host "COLLECTOR_PENDING_TOTAL=$($sender.pending_total_count)"
            Write-Host "COLLECTOR_SENT_PER_SEC=$($sender.sent_per_sec)"
            Write-Host "COLLECTOR_COALESCED_TRADE=$($sender.coalesced_trade_overwrite_count)"
            Write-Host "COLLECTOR_LAST_ERROR=$($sender.last_error)"
        }
    } catch {
        Write-Host "WORKER_HEALTH=False"
        Write-Host "ERROR=$($_.Exception.Message)"
    }
}

if ($Action -eq "start") { Start-V2 $false; exit 0 }
if ($Action -eq "start-fast") { Start-V2 $true; exit 0 }
if ($Action -eq "stop") { Stop-V2; exit 0 }
if ($Action -eq "restart") { Stop-V2; Start-V2 $false; exit 0 }
if ($Action -eq "restart-fast") { Stop-V2; Start-V2 $true; exit 0 }
if ($Action -eq "status") { Status-V2; exit 0 }
if ($Action -eq "ahk") { [void](Start-HtsBridge); exit 0 }
throw "unknown action: $Action"
