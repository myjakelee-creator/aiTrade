param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "stop", "restart", "status", "start-fast", "restart-fast", "doctor")]
    [string]$Action
)

$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$Python32 = "C:\Users\myjay\AppData\Local\Programs\Python\Python310-32\python.exe"
$WorkerPidFile = Join-Path $RuntimeDir "worker64.pid"
$CollectorPidFile = Join-Path $RuntimeDir "collector32.pid"
$ContextPidFile = Join-Path $RuntimeDir "context_snapshot_writer.pid"
$WorkerUrl = "http://127.0.0.1:8765/api/v2/health"
$SnapshotUrl = "http://127.0.0.1:8765/api/v2/snapshot?limit=1"
$BoardUrl = "http://127.0.0.1:8765/"
$OldLauncher = Join-Path $ProjectRoot "stockboard_v2_live.cmd"
$UniverseFile = Join-Path $RuntimeDir "universe.json"
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

function Get-PythonBits([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return 0 }
    try {
        $value = & $Path -c "import struct; print(struct.calcsize('P') * 8)" 2>$null
        return [int]([string]$value).Trim()
    } catch {
        return 0
    }
}

function Resolve-Python64 {
    $candidates = New-Object System.Collections.Generic.List[string]

    if ($env:STOCKBOARD_PYTHON64) {
        $candidates.Add([string]$env:STOCKBOARD_PYTHON64)
    }

    try {
        $command = Get-Command python -ErrorAction Stop
        if ($command.Source) { $candidates.Add([string]$command.Source) }
    } catch { }

    foreach ($path in @(
        "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
        "C:\Python*\python.exe",
        "C:\Program Files\Python*\python.exe"
    )) {
        Get-ChildItem -Path $path -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            ForEach-Object { $candidates.Add($_.FullName) }
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ((Get-PythonBits $candidate) -eq 64) {
            return $candidate
        }
    }

    throw "64-bit Python was not found. Set STOCKBOARD_PYTHON64 to a 64-bit python.exe."
}

function Assert-Python32 {
    if (-not (Test-Path -LiteralPath $Python32)) {
        throw "32-bit Python not found: $Python32"
    }
    $bits = Get-PythonBits $Python32
    if ($bits -ne 32) {
        throw "Collector Python must be 32-bit, but detected ${bits}bit: $Python32"
    }
}

function Stop-PidFile([string]$PidFile, [string]$Name) {
    if (-not (Test-Path -LiteralPath $PidFile)) { return }

    $raw = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $pidNumber = 0
    if ([int]::TryParse([string]$raw, [ref]$pidNumber)) {
        $process = Get-Process -Id $pidNumber -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            Write-Host "Stopping $Name PID=$pidNumber"
            Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Stop-Port([int]$Port) {
    try {
        $pids = @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
    } catch {
        $pids = @()
    }

    foreach ($pidNumber in $pids) {
        if ($pidNumber -gt 0 -and $pidNumber -ne $PID) {
            Write-Host "Stopping port $Port listener PID=$pidNumber"
            Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
        }
    }
}

function Stop-KnownV2Processes {
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

    try {
        $rows = @(
            Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    $commandLine = [string]$_.CommandLine
                    if (-not $commandLine) { return $false }
                    foreach ($pattern in $patterns) {
                        if ($commandLine -like "*$pattern*") { return $true }
                    }
                    return $false
                } |
                Select-Object ProcessId, Name, CommandLine
        )
    } catch {
        $rows = @()
    }

    foreach ($row in $rows) {
        $pidNumber = [int]$row.ProcessId
        if ($pidNumber -gt 0 -and $pidNumber -ne $PID) {
            Write-Host "Stopping v2 process PID=$pidNumber NAME=$($row.Name)"
            Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-OpstarterRows {
    try {
        return @(
            Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    $name = [string]$_.Name
                    $commandLine = [string]$_.CommandLine
                    $name -match '^(?i)opstarter.*\.exe$' -or
                        $commandLine -match '(?i)opstarter'
                } |
                Select-Object ProcessId, Name, CommandLine
        )
    } catch {
        return @()
    }
}

function Report-OpstarterState([string]$Stage) {
    $rows = @(Get-OpstarterRows)
    Write-Host "OPSTARTER_STAGE=$Stage COUNT=$($rows.Count)"
    foreach ($row in $rows) {
        Write-Host "OPSTARTER PID=$($row.ProcessId) NAME=$($row.Name)"
    }
    # Do not terminate opstarter. It owns the OpenAPI login dialog and its HWND.
}

function Stop-V2 {
    Ensure-RuntimeDir
    Write-Step "Stopping StockBoard v2 processes"

    Stop-PidFile $CollectorPidFile "collector32"
    Start-Sleep -Milliseconds 500
    Stop-PidFile $WorkerPidFile "worker64"
    Stop-PidFile $ContextPidFile "context_snapshot_writer"
    Stop-KnownV2Processes
    Stop-Port 8765
    Stop-Port 8710

    Remove-Item -LiteralPath $WorkerPidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $CollectorPidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ContextPidFile -Force -ErrorAction SilentlyContinue

    Start-Sleep -Milliseconds 700
    Report-OpstarterState "after_stop_no_force_kill"
}

function Test-WorkerReady {
    try {
        Invoke-RestMethod -Uri $WorkerUrl -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-WorkerReady([int]$TimeoutSec = 15) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-WorkerReady) { return $true }
        Start-Sleep -Milliseconds 300
    }
    return $false
}

function Get-CollectorLoginState {
    try {
        $snapshot = Invoke-RestMethod -Uri $SnapshotUrl -TimeoutSec 3
        $collector = $snapshot.status.collector_status
        $provider = $collector.status

        return [pscustomobject]@{
            Snapshot = $snapshot
            Collector = $collector
            Provider = $provider
            LoginState = [string]$provider.login_state
            NativeHandleReady = [bool]$provider.openapi_native_handle_ready
            NativeHwnd = $provider.openapi_native_hwnd
            ProviderStarted = [bool]$collector.provider_started
            RegisteredCount = [int]($collector.registered_count)
            LastError = [string]$provider.last_error
        }
    } catch {
        return $null
    }
}

function Wait-CollectorOpenApiReady([int]$TimeoutSec = 180) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lastState = ""

    while ((Get-Date) -lt $deadline) {
        $state = Get-CollectorLoginState
        if ($null -ne $state) {
            $summary = "login=$($state.LoginState) hwnd_ready=$($state.NativeHandleReady) registered=$($state.RegisteredCount)"
            if ($summary -ne $lastState) {
                Write-Host "OPENAPI_WAIT $summary"
                $lastState = $summary
            }

            if (
                $state.ProviderStarted -and
                $state.LoginState -eq "connected" -and
                $state.NativeHandleReady -and
                $state.RegisteredCount -gt 0
            ) {
                Write-Host "COLLECTOR_OPENAPI_READY=True registered_count=$($state.RegisteredCount) hwnd=$($state.NativeHwnd)"
                return $true
            }
        }

        Start-Sleep -Milliseconds 500
    }

    Write-Warning "OpenAPI login was not confirmed within ${TimeoutSec}s. The login helper was left untouched."
    Report-OpstarterState "login_timeout_no_force_kill"
    return $false
}

function Build-Universe([string]$Python64) {
    Write-Step "Building v2 universe"
    & $Python64 (Join-Path $ProjectRoot "realtime_v2\build_universe.py") --limit 300 --rank-basis today

    if ($LASTEXITCODE -eq 0) { return }

    if (Test-Path -LiteralPath $UniverseFile) {
        Write-Warning "build_universe failed with exit code $LASTEXITCODE; using existing universe.json"
        return
    }

    throw "build_universe failed and no existing universe.json is available"
}

function Start-V2([bool]$FastOpen) {
    Ensure-RuntimeDir
    $python64 = Resolve-Python64
    Assert-Python32

    Write-Host "PYTHON64=$python64"
    Write-Host "PYTHON64_BITS=$(Get-PythonBits $python64)"
    Write-Host "PYTHON32=$Python32"
    Write-Host "PYTHON32_BITS=$(Get-PythonBits $Python32)"

    Stop-V2
    Build-Universe $python64

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $workerOut = Join-Path $RuntimeDir "worker64_large_$stamp.out.log"
    $workerErr = Join-Path $RuntimeDir "worker64_large_$stamp.err.log"
    $collectorOut = Join-Path $RuntimeDir "collector32_large_$stamp.out.log"
    $collectorErr = Join-Path $RuntimeDir "collector32_large_$stamp.err.log"
    $contextOut = Join-Path $RuntimeDir "context_snapshot_$stamp.out.log"
    $contextErr = Join-Path $RuntimeDir "context_snapshot_$stamp.err.log"

    Write-Step "Starting low-priority context snapshot writer"
    $context = Start-Process `
        -FilePath $python64 `
        -ArgumentList @("realtime_v2\context_snapshot_writer.py", "--interval-sec", "30", "--ohlc-bootstrap") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $contextOut `
        -RedirectStandardError $contextErr `
        -PassThru
    Set-Content -LiteralPath $ContextPidFile -Value $context.Id -Encoding ASCII

    Write-Step "Starting 64-bit worker"
    $worker = Start-Process `
        -FilePath $python64 `
        -ArgumentList @("realtime_v2\worker64_guarded_large_bidask.py") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $workerOut `
        -RedirectStandardError $workerErr `
        -PassThru
    Set-Content -LiteralPath $WorkerPidFile -Value $worker.Id -Encoding ASCII
    Write-Host "WORKER64_PID=$($worker.Id)"

    if (-not (Wait-WorkerReady 15)) {
        throw "64-bit worker did not become ready. Check $workerErr"
    }

    Write-Step "Starting 32-bit OpenAPI collector"
    $collectorArgs = @(
        "realtime_v2\collector32_large_bidask.py",
        "--limit", "300",
        "--suffix", "AL",
        "--flush-ms", "50"
    )

    if ($FastOpen) {
        Write-Host "FAST_OPEN=True"
        Write-Host "ORDERBOOK_REALTIME=False"
    } else {
        $collectorArgs += "--orderbook"
        Write-Host "FAST_OPEN=False"
        Write-Host "ORDERBOOK_REALTIME=True"
    }

    $oldHide = $env:STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN
    $env:STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN = "1"
    try {
        $collector = Start-Process `
            -FilePath $Python32 `
            -ArgumentList $collectorArgs `
            -WorkingDirectory $ProjectRoot `
            -WindowStyle Normal `
            -RedirectStandardOutput $collectorOut `
            -RedirectStandardError $collectorErr `
            -PassThru
    } finally {
        if ($null -eq $oldHide) {
            Remove-Item Env:\STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN -ErrorAction SilentlyContinue
        } else {
            $env:STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN = $oldHide
        }
    }

    Set-Content -LiteralPath $CollectorPidFile -Value $collector.Id -Encoding ASCII
    Write-Host "COLLECTOR32_PID=$($collector.Id)"
    Write-Host "COLLECTOR32_STDERR=$collectorErr"

    Write-Step "Waiting for OpenAPI login and registration"
    $openApiReady = Wait-CollectorOpenApiReady 180

    if ($openApiReady) {
        Write-Step "Starting HTS bridge"
        if (Test-Path -LiteralPath $OldLauncher) {
            & cmd.exe /c "`"$OldLauncher`" ahk"
        }
    } else {
        Write-Warning "HTS bridge startup was skipped until OpenAPI login is confirmed."
    }

    Start-Process $BoardUrl
    Write-Host ""
    Write-Host "Open $BoardUrl"
}

function Show-Status {
    Ensure-RuntimeDir
    $python64 = $null
    try { $python64 = Resolve-Python64 } catch { }

    Write-Host "PYTHON64=$python64"
    if ($python64) { Write-Host "PYTHON64_BITS=$(Get-PythonBits $python64)" }
    Write-Host "PYTHON32=$Python32"
    Write-Host "PYTHON32_BITS=$(Get-PythonBits $Python32)"

    try {
        $snapshot = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/v2/snapshot?limit=5" -TimeoutSec 10
        $collector = $snapshot.status.collector_status
        $provider = $collector.status

        Write-Host "WORKER_HEALTH=True"
        Write-Host "ROW_COUNT=$($snapshot.row_count)"
        Write-Host "EVENT_COUNT=$($snapshot.status.event_count)"
        Write-Host "TRADE_COUNT=$($snapshot.status.trade_count)"
        Write-Host "LOGIN_STATE=$($provider.login_state)"
        Write-Host "OPENAPI_NATIVE_HANDLE_READY=$($provider.openapi_native_handle_ready)"
        Write-Host "OPENAPI_NATIVE_HWND=$($provider.openapi_native_hwnd)"
        Write-Host "COLLECTOR_PROVIDER_STARTED=$($collector.provider_started)"
        Write-Host "COLLECTOR_REGISTERED_COUNT=$($collector.registered_count)"
        Write-Host "COLLECTOR_PENDING_TOTAL=$($collector.sender_stats.pending_total_count)"
        Write-Host "COLLECTOR_SENT_PER_SEC=$($collector.sender_stats.sent_per_sec)"
        Write-Host "OPENAPI_LAST_ERROR=$($provider.last_error)"
    } catch {
        Write-Host "WORKER_HEALTH=False"
        Write-Host "ERROR=$($_.Exception.Message)"
    }

    Report-OpstarterState "status_only_no_force_kill"
}

function Invoke-Doctor {
    Ensure-RuntimeDir
    $lines = New-Object System.Collections.Generic.List[string]

    function Add-Line([string]$Text) {
        $lines.Add($Text) | Out-Null
        Write-Host $Text
    }

    Add-Line "StockBoard v2 safe launcher doctor"
    Add-Line "TIME=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

    try {
        Add-Line "GIT_BRANCH=$(& git -C $ProjectRoot branch --show-current 2>$null)"
        Add-Line "GIT_HEAD=$(& git -C $ProjectRoot rev-parse --short HEAD 2>$null)"
    } catch {
        Add-Line "GIT_INFO_ERROR=$($_.Exception.Message)"
    }

    try {
        $python64 = Resolve-Python64
        Add-Line "PYTHON64=$python64"
        Add-Line "PYTHON64_BITS=$(Get-PythonBits $python64)"
    } catch {
        Add-Line "PYTHON64_ERROR=$($_.Exception.Message)"
    }

    Add-Line "PYTHON32=$Python32"
    Add-Line "PYTHON32_BITS=$(Get-PythonBits $Python32)"

    $opstarterRows = @(Get-OpstarterRows)
    Add-Line "OPSTARTER_PROCESS_COUNT=$($opstarterRows.Count)"
    foreach ($row in $opstarterRows) {
        Add-Line "OPSTARTER PID=$($row.ProcessId) NAME=$($row.Name)"
    }

    try {
        $state = Get-CollectorLoginState
        if ($null -eq $state) {
            Add-Line "COLLECTOR_LOGIN_STATUS=False"
        } else {
            Add-Line "COLLECTOR_LOGIN_STATUS=True"
            Add-Line "LOGIN_STATE=$($state.LoginState)"
            Add-Line "OPENAPI_NATIVE_HANDLE_READY=$($state.NativeHandleReady)"
            Add-Line "OPENAPI_NATIVE_HWND=$($state.NativeHwnd)"
            Add-Line "REGISTERED_COUNT=$($state.RegisteredCount)"
            Add-Line "OPENAPI_LAST_ERROR=$($state.LastError)"
        }
    } catch {
        Add-Line "COLLECTOR_LOGIN_ERROR=$($_.Exception.Message)"
    }

    foreach ($pattern in @("worker64_large_*.err.log", "collector32_large_*.err.log")) {
        $file = Get-ChildItem -Path $RuntimeDir -Filter $pattern -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($file) {
            Add-Line "LOG=$($file.FullName)"
            foreach ($line in (Get-Content -LiteralPath $file.FullName -Tail 40 -ErrorAction SilentlyContinue)) {
                Add-Line $line
            }
        }
    }

    Set-Content -LiteralPath $DoctorReport -Value $lines -Encoding UTF8
    Write-Host "DOCTOR_REPORT=$DoctorReport" -ForegroundColor Cyan
}

switch ($Action) {
    "start" { Start-V2 $false; break }
    "start-fast" { Start-V2 $true; break }
    "restart" { Start-V2 $false; break }
    "restart-fast" { Start-V2 $true; break }
    "stop" { Stop-V2; break }
    "status" { Show-Status; break }
    "doctor" { Invoke-Doctor; break }
}
