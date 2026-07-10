@echo off
setlocal EnableExtensions
cd /d C:\aiTrade

set "ACTION=%~1"
if "%ACTION%"=="" goto menu
goto run

:menu
echo.
echo StockBoard v2 Realtime - Large Trade / BidAsk Last Cache
echo.
echo   1 Start large normal
echo   2 Stop v2
echo   3 Restart large normal
echo   4 Status large
echo   5 Start large fast-open
echo   6 Restart large fast-open
echo   7 Doctor large
echo   0 Exit
echo.
set /p "CHOICE=Select: "
if "%CHOICE%"=="1" set "ACTION=start"
if "%CHOICE%"=="2" set "ACTION=stop"
if "%CHOICE%"=="3" set "ACTION=restart"
if "%CHOICE%"=="4" set "ACTION=status"
if "%CHOICE%"=="5" set "ACTION=start-fast"
if "%CHOICE%"=="6" set "ACTION=restart-fast"
if "%CHOICE%"=="7" set "ACTION=doctor"
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
$SnapshotUrl = "http://127.0.0.1:8765/api/v2/snapshot?limit=300"
$BoardUrl = "http://127.0.0.1:8765/"
$OldLauncher = Join-Path $ProjectRoot "stockboard_v2_live.cmd"
$DoctorReport = Join-Path $RuntimeDir "large_doctor_report.txt"

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
        if ($pidNumber -gt 0 -and $pidNumber -ne $PID) {
            Write-Host "Stopping port $Port listener PID=$pidNumber"
            Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
        }
    }
}

function Stop-ProcessRows([object[]]$Rows, [string]$Label) {
    foreach ($row in $Rows) {
        $pidNumber = [int]$row.ProcessId
        if ($pidNumber -le 0 -or $pidNumber -eq $PID) { continue }
        try {
            Write-Host "Stopping $Label PID=$pidNumber NAME=$($row.Name)"
            Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
        } catch { }
    }
}

function Get-OpstarterProcessRows {
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $name = [string]$_.Name
            $cmd = [string]$_.CommandLine
            return ($name -match '^(?i)opstarter.*\.exe$' -or $cmd -match '(?i)opstarter')
        } | Select-Object ProcessId, Name, CommandLine)
    } catch {
        return @()
    }
}

function Stop-OpenApiStarterArtifacts {
    # Kiwoom OpenAPI sometimes leaves an opstarter error dialog/process after a
    # fast restart.  Kill only opstarter, never HTS/Hero main windows.
    $rows = @(Get-OpstarterProcessRows)
    if ($rows.Count -gt 0) {
        Stop-ProcessRows $rows "opstarter"
        Start-Sleep -Milliseconds 500
    }
}

function Stop-V2PythonAndAhkProcesses {
    try {
        $patterns = @(
            "realtime_v2\collector32_large_bidask.py",
            "realtime_v2\collector32_large.py",
            "realtime_v2\collector32.py",
            "realtime_v2\worker64_guarded_large_bidask.py",
            "realtime_v2\worker64_guarded_large.py",
            "realtime_v2\worker64_guarded_large_hotfix.py",
            "realtime_v2\worker64_guarded.py",
            "realtime_v2\worker64.py",
            "realtime_v2\context_snapshot_writer.py",
            "scripts\stockboard_kiwoom_link_v1.ahk"
        )
        $rows = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $cmd = [string]$_.CommandLine
            if (-not $cmd) { return $false }
            foreach ($pattern in $patterns) {
                if ($cmd -like "*$pattern*") { return $true }
            }
            return $false
        } | Select-Object ProcessId, Name, CommandLine)
        Stop-ProcessRows $rows "v2 process"
    } catch { }
}

function Stop-OrphanStockBoardConsoles {
    try {
        $self = $PID
        $rows = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $name = [string]$_.Name
            $cmd = [string]$_.CommandLine
            if (-not $cmd) { return $false }
            if ($_.ProcessId -eq $self) { return $false }
            if ($name -notin @("cmd.exe", "conhost.exe", "powershell.exe", "pwsh.exe")) { return $false }
            return ($cmd -like "*stockboard_v2*" -or $cmd -like "*StockBoard v2*" -or $cmd -like "*C:\aiTrade*data\runtime\stockboard_v2*")
        } | Select-Object ProcessId, Name, CommandLine)
        Stop-ProcessRows $rows "orphan console"
    } catch { }
}

function Clear-RuntimePidFiles {
    Remove-Item -LiteralPath $WorkerPidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $CollectorPidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ContextPidFile -Force -ErrorAction SilentlyContinue
}

function Stop-LargeProcesses {
    Ensure-RuntimeDir
    Stop-PidFile $CollectorPidFile "collector32"
    Stop-PidFile $WorkerPidFile "worker64"
    Stop-PidFile $ContextPidFile "context_snapshot_writer"
    Stop-V2PythonAndAhkProcesses
    Stop-OpenApiStarterArtifacts
    Stop-Port 8765
    Stop-Port 8710
    Start-Sleep -Milliseconds 500
    Stop-OrphanStockBoardConsoles
    Stop-OpenApiStarterArtifacts
    Clear-RuntimePidFiles
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
        Invoke-RestMethod -Uri $WorkerUrl -TimeoutSec 3 | Out-Null
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
            $snapshot = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/v2/snapshot?limit=1" -TimeoutSec 3
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

    Write-Step "Starting guarded worker with large-trade and bidask support"
    $worker = Start-Process -FilePath $Python64 -ArgumentList @("realtime_v2\worker64_guarded_large_bidask.py") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $workerOut -RedirectStandardError $workerErr -PassThru
    Set-Content -LiteralPath $WorkerPidFile -Value $worker.Id -Encoding ASCII
    Write-Host "WORKER64_PID=$($worker.Id)"
    Write-Host "WORKER64_STDOUT=$workerOut"
    Write-Host "WORKER64_STDERR=$workerErr"

    if (-not (Wait-WorkerReady 10)) {
        Write-Warning "worker did not respond yet; collector will still be started"
    }

    Write-Step "Preparing Kiwoom OpenAPI starter"
    Stop-OpenApiStarterArtifacts
    Start-Sleep -Milliseconds 1200

    Write-Step "Starting 32-bit collector with large-trade and thin bidask scheduler"
    if (-not (Test-Path -LiteralPath $Python32)) { throw "32-bit Python not found: $Python32" }
    $collectorArgs = @("realtime_v2\collector32_large_bidask.py", "--limit", "300", "--suffix", "AL", "--flush-ms", "50")
    if ($FastOpen) {
        Write-Host "FAST_OPEN=True"
        Write-Host "ORDERBOOK_REALTIME=False"
        Write-Host "BIDASK_THIN=True"
    } else {
        $collectorArgs += "--orderbook"
        Write-Host "FAST_OPEN=False"
        Write-Host "ORDERBOOK_REALTIME=True"
        Write-Host "BIDASK_THIN=True"
    }

    $oldHideCollectorConsole = $env:STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN
    $env:STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN = "1"
    try {
        $collector = Start-Process -FilePath $Python32 -ArgumentList $collectorArgs -WorkingDirectory $ProjectRoot -WindowStyle Normal -RedirectStandardOutput $collectorOut -RedirectStandardError $collectorErr -PassThru
    } finally {
        if ($null -eq $oldHideCollectorConsole) {
            Remove-Item Env:\STOCKBOARD_HIDE_COLLECTOR_CONSOLE -ErrorAction SilentlyContinue
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
    Stop-OpenApiStarterArtifacts

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
        $snapshot = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/v2/snapshot?limit=5" -TimeoutSec 10
        Write-Host "WORKER_HEALTH=True"
        Write-Host "ROW_COUNT=$($snapshot.row_count)"
        Write-Host "EVENT_COUNT=$($snapshot.status.event_count)"
        Write-Host "TRADE_COUNT=$($snapshot.status.trade_count)"
        Write-Host "ORDERBOOK_COUNT=$($snapshot.status.orderbook_count)"
        Write-Host "BIDASK_CACHE_COUNT=$($snapshot.status.bidask_cache_count)"
        Write-Host "BIDASK_CACHE_APPLIED_ROWS=$($snapshot.status.bidask_cache_applied_rows)"
        Write-Host "DISPLAY_HOLD_ACTIVE=$($snapshot.status.display_hold_active)"
        Write-Host "DISPLAY_HOLD_APPLIED_ROWS=$($snapshot.status.display_hold_applied_rows)"
        $opRows = @(Get-OpstarterProcessRows)
        Write-Host "OPSTARTER_PROCESS_COUNT=$($opRows.Count)"
        $collectorStatus = $snapshot.status.collector_status
        $sender = $collectorStatus.sender_stats
        if ($sender) {
            Write-Host "COLLECTOR_CONNECTED=$($sender.connected)"
            Write-Host "COLLECTOR_PENDING_TOTAL=$($sender.pending_total_count)"
            Write-Host "COLLECTOR_SENT_PER_SEC=$($sender.sent_per_sec)"
            Write-Host "COLLECTOR_LARGE_BUY_COUNT=$($sender.large_trade_buy_count)"
            Write-Host "COLLECTOR_LARGE_SELL_COUNT=$($sender.large_trade_sell_count)"
        } else {
            Write-Host "COLLECTOR_SENDER_STATS=False"
        }
        if ($collectorStatus.status.orderbook_thin_scheduler) {
            $sched = $collectorStatus.status.orderbook_thin_scheduler
            Write-Host "BIDASK_THIN_ALIVE=$($sched.alive)"
            Write-Host "BIDASK_THIN_LAST_CODE=$($sched.last_enqueued_code)"
            Write-Host "BIDASK_THIN_LAST_LANE=$($sched.last_enqueued_lane)"
            Write-Host "BIDASK_THIN_ENQUEUE_COUNT=$($sched.enqueue_count)"
        }
    } catch {
        Write-Host "WORKER_HEALTH=False"
        Write-Host "ERROR=$($_.Exception.Message)"
    }
}

function Add-DoctorTail([scriptblock]$AddLine, [string]$Pattern, [string]$Label) {
    $file = Get-ChildItem -Path $RuntimeDir -Filter $Pattern -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $file) {
        & $AddLine "$Label=NO_LOG_FILE"
        return
    }
    & $AddLine "$Label=$($file.FullName)"
    & $AddLine "---- ${Label} tail ----"
    $tail = Get-Content -LiteralPath $file.FullName -Tail 80 -ErrorAction SilentlyContinue
    if ($tail) {
        foreach ($line in $tail) { & $AddLine $line }
    } else {
        & $AddLine "(empty)"
    }
}

function Doctor-V2Large {
    Ensure-RuntimeDir
    $lines = New-Object System.Collections.Generic.List[string]
    function Add-Line([string]$Message) {
        $lines.Add($Message) | Out-Null
        Write-Host $Message
    }

    Add-Line "StockBoard v2 large doctor"
    Add-Line "TIME=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Add-Line "PROJECT_ROOT=$ProjectRoot"
    Add-Line "REPORT=$DoctorReport"

    try {
        $head = (& git -C $ProjectRoot rev-parse --short HEAD 2>$null)
        $branch = (& git -C $ProjectRoot branch --show-current 2>$null)
        Add-Line "GIT_BRANCH=$branch"
        Add-Line "GIT_HEAD=$head"
    } catch {
        Add-Line "GIT_INFO_ERROR=$($_.Exception.Message)"
    }

    Add-Line "PYTHON64=$Python64"
    Add-Line "PYTHON32=$Python32"
    Add-Line "WORKER_PID_FILE_EXISTS=$(Test-Path -LiteralPath $WorkerPidFile)"
    if (Test-Path -LiteralPath $WorkerPidFile) { Add-Line "WORKER_PID=$(Get-Content -LiteralPath $WorkerPidFile | Select-Object -First 1)" }
    Add-Line "COLLECTOR_PID_FILE_EXISTS=$(Test-Path -LiteralPath $CollectorPidFile)"
    if (Test-Path -LiteralPath $CollectorPidFile) { Add-Line "COLLECTOR_PID=$(Get-Content -LiteralPath $CollectorPidFile | Select-Object -First 1)" }
    Add-Line "HAS_COLLECTOR_LARGE_BIDASK=$(Test-Path -LiteralPath (Join-Path $ProjectRoot 'realtime_v2\collector32_large_bidask.py'))"
    Add-Line "HAS_WORKER_LARGE_BIDASK=$(Test-Path -LiteralPath (Join-Path $ProjectRoot 'realtime_v2\worker64_guarded_large_bidask.py'))"
    $opRows = @(Get-OpstarterProcessRows)
    Add-Line "OPSTARTER_PROCESS_COUNT=$($opRows.Count)"
    foreach ($row in $opRows) {
        Add-Line "OPSTARTER PID=$($row.ProcessId) NAME=$($row.Name) CMD=$($row.CommandLine)"
    }

    try {
        & $Python64 -m py_compile `
            (Join-Path $ProjectRoot "realtime_v2\collector32_large.py") `
            (Join-Path $ProjectRoot "realtime_v2\collector32_large_bidask.py") `
            (Join-Path $ProjectRoot "realtime_v2\orderbook_thin_scheduler.py") `
            (Join-Path $ProjectRoot "realtime_v2\worker64_guarded_large.py") `
            (Join-Path $ProjectRoot "realtime_v2\worker64_guarded_large_bidask.py") `
            (Join-Path $ProjectRoot "realtime_v2\bidask_last_cache_patch.py") `
            (Join-Path $ProjectRoot "realtime_v2\display_hold_policy_patch.py") `
            (Join-Path $ProjectRoot "realtime_v2\display_hold_ohlc_price_patch.py") `
            (Join-Path $ProjectRoot "realtime_v2\html_header_sort_patch.py")
        Add-Line "PY_COMPILE=True"
    } catch {
        Add-Line "PY_COMPILE=False"
        Add-Line "PY_COMPILE_ERROR=$($_.Exception.Message)"
    }

    try {
        $procRows = @(Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.CommandLine -and ($_.CommandLine -like "*realtime_v2\collector32_large_bidask.py*" -or $_.CommandLine -like "*realtime_v2\collector32_large.py*" -or $_.CommandLine -like "*realtime_v2\worker64_guarded_large_bidask.py*" -or $_.CommandLine -like "*realtime_v2\worker64_guarded_large.py*") } |
            Select-Object ProcessId, Name, CommandLine)
        Add-Line "MATCHED_PROCESS_COUNT=$($procRows.Count)"
        foreach ($p in $procRows) {
            Add-Line "PROCESS PID=$($p.ProcessId) NAME=$($p.Name) CMD=$($p.CommandLine)"
        }
    } catch {
        Add-Line "PROCESS_LOOKUP_ERROR=$($_.Exception.Message)"
    }

    try {
        $snapshot = Invoke-RestMethod -Uri $SnapshotUrl -TimeoutSec 30
        Add-Line "SNAPSHOT=True"
        Add-Line "ROW_COUNT=$($snapshot.row_count)"
        Add-Line "EVENT_COUNT=$($snapshot.status.event_count)"
        Add-Line "TRADE_COUNT=$($snapshot.status.trade_count)"
        Add-Line "ORDERBOOK_COUNT=$($snapshot.status.orderbook_count)"
        Add-Line "BIDASK_CACHE_COUNT=$($snapshot.status.bidask_cache_count)"
        Add-Line "BIDASK_CACHE_APPLIED_ROWS=$($snapshot.status.bidask_cache_applied_rows)"
        Add-Line "DISPLAY_HOLD_ACTIVE=$($snapshot.status.display_hold_active)"
        Add-Line "DISPLAY_HOLD_APPLIED_ROWS=$($snapshot.status.display_hold_applied_rows)"
        Add-Line "DISPLAY_HOLD_APPLIED_FIELDS=$($snapshot.status.display_hold_applied_fields)"
        $collectorStatus = $snapshot.status.collector_status
        if ($collectorStatus) {
            Add-Line "COLLECTOR_PROVIDER_STARTED=$($collectorStatus.provider_started)"
            Add-Line "COLLECTOR_REGISTERED_COUNT=$($collectorStatus.registered_count)"
            Add-Line "ORDERBOOK_THIN_SCHEDULER=$($collectorStatus.status.orderbook_thin_scheduler | ConvertTo-Json -Compress -Depth 5)"
        } else {
            Add-Line "COLLECTOR_STATUS=False"
        }
    } catch {
        Add-Line "SNAPSHOT=False"
        Add-Line "SNAPSHOT_ERROR=$($_.Exception.Message)"
    }

    Add-DoctorTail ${function:Add-Line} "worker64_large_*.err.log" "WORKER_LARGE_ERR_LOG"
    Add-DoctorTail ${function:Add-Line} "collector32_large_*.err.log" "COLLECTOR_LARGE_ERR_LOG"
    Add-DoctorTail ${function:Add-Line} "display_hold*_error.txt" "DISPLAY_HOLD_ERROR_LOG"
    Add-DoctorTail ${function:Add-Line} "bidask*_error.txt" "BIDASK_ERROR_LOG"
    Add-DoctorTail ${function:Add-Line} "html_header_sort_patch_error.txt" "HEADER_SORT_ERROR_LOG"

    Set-Content -LiteralPath $DoctorReport -Value $lines -Encoding UTF8
    Write-Host ""
    Write-Host "DOCTOR_REPORT=$DoctorReport" -ForegroundColor Cyan
}

if ($Action -eq "start") { Start-V2Large $false; exit 0 }
if ($Action -eq "start-fast") { Start-V2Large $true; exit 0 }
if ($Action -eq "restart") { Start-V2Large $false; exit 0 }
if ($Action -eq "restart-fast") { Start-V2Large $true; exit 0 }
if ($Action -eq "stop") { Stop-V2; exit 0 }
if ($Action -eq "status") { Status-V2Large; exit 0 }
if ($Action -eq "doctor") { Doctor-V2Large; exit 0 }
throw "unknown action: $Action"
