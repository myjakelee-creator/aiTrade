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
$ContextOwnerStatusFile = Join-Path $RuntimeDir "context_owner_status.json"
$ContextLauncher = Join-Path $ProjectRoot "scripts\start_context_singleflight.ps1"
$WorkerUrl = "http://127.0.0.1:8765/api/v2/health"
$SnapshotUrl = "http://127.0.0.1:8765/api/v2/snapshot?limit=1"
$BoardUrl = "http://127.0.0.1:8765/"
$OldLauncher = Join-Path $ProjectRoot "stockboard_v2_live.cmd"
$UniverseFile = Join-Path $RuntimeDir "universe.json"
$DoctorReport = Join-Path $RuntimeDir "large_doctor_report.txt"
$ContextWriterPattern = '(?i)realtime_v2[\\.]context_snapshot_writer(?:_(?:base|singleflight|portable(?:_v2)?))?(?:\.py)?'

Set-Location -LiteralPath $ProjectRoot

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
    foreach ($pattern in @(
        "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
        "C:\Python*\python.exe",
        "C:\Program Files\Python*\python.exe"
    )) {
        Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            ForEach-Object { $candidates.Add($_.FullName) }
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ((Get-PythonBits $candidate) -eq 64) { return $candidate }
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

function Read-Pid([string]$PidFile) {
    if (-not (Test-Path -LiteralPath $PidFile)) { return 0 }
    $raw = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $number = 0
    if ([int]::TryParse([string]$raw, [ref]$number)) { return $number }
    return 0
}

function Test-PidAlive([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Stop-PidFile([string]$PidFile, [string]$Name) {
    $pidNumber = Read-Pid $PidFile
    if (Test-PidAlive $pidNumber) {
        Write-Host "Stopping $Name PID=$pidNumber"
        Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
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

function Get-ContextWriterRows {
    try {
        return @(
            Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    $name = [string]$_.Name
                    $commandLine = [string]$_.CommandLine
                    if ($name -notmatch '^(?i)python(w)?\.exe$' -or -not $commandLine) {
                        return $false
                    }
                    return $commandLine -match $ContextWriterPattern
                } |
                Select-Object ProcessId, Name, CommandLine
        )
    } catch {
        return @()
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
    $rows += @(Get-ContextWriterRows)
    $seen = @{}
    foreach ($row in $rows) {
        $pidNumber = [int]$row.ProcessId
        if ($pidNumber -le 0 -or $pidNumber -eq $PID -or $seen.ContainsKey($pidNumber)) {
            continue
        }
        $seen[$pidNumber] = $true
        Write-Host "Stopping v2 process PID=$pidNumber NAME=$($row.Name)"
        Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
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
    # Do not terminate opstarter after collector start. It owns the login dialog/HWND.
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
    Remove-Item -LiteralPath $ContextOwnerStatusFile -Force -ErrorAction SilentlyContinue
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
    $collectorPid = Read-Pid $CollectorPidFile
    $collectorAlive = Test-PidAlive $collectorPid
    try {
        $snapshot = Invoke-RestMethod -Uri $SnapshotUrl -TimeoutSec 3
        $collector = $snapshot.status.collector_status
        $provider = $collector.status
        $realRegSucceeded = [bool]$provider.realreg_succeeded
        $actualCount = [int]($provider.realreg_code_count)
        if ($actualCount -le 0 -and $realRegSucceeded) {
            $actualCount = [int]($collector.registered_count)
        }
        return [pscustomobject]@{
            Snapshot = $snapshot
            Collector = $collector
            Provider = $provider
            CollectorPid = $collectorPid
            CollectorAlive = $collectorAlive
            LoginState = [string]$provider.login_state
            RealRegSucceeded = $realRegSucceeded
            RegisteredCount = $actualCount
            NativeHandleReady = [bool]$provider.openapi_native_handle_ready
            NativeHwnd = $provider.openapi_native_hwnd
            ProviderStarted = [bool]$collector.provider_started
            RealData = [int]($provider.realdata_received_count)
            TradeReceived = [int]($provider.trade_event_received_count)
            RealDataLastAt = $provider.realdata_last_received_at
            WorkerTrades = [int]($snapshot.status.trade_count)
            WorkerLastEventAt = $snapshot.status.last_event_at
            LastError = [string]$provider.last_error
        }
    } catch {
        return [pscustomobject]@{
            CollectorPid = $collectorPid
            CollectorAlive = $collectorAlive
            LoginState = "unavailable"
            RealRegSucceeded = $false
            RegisteredCount = 0
            NativeHandleReady = $false
            NativeHwnd = $null
            ProviderStarted = $false
            RealData = 0
            TradeReceived = 0
            RealDataLastAt = $null
            WorkerTrades = 0
            WorkerLastEventAt = $null
            LastError = $_.Exception.Message
        }
    }
}

function Wait-CollectorOpenApiReady([int]$TimeoutSec = 180) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lastState = ""
    while ((Get-Date) -lt $deadline) {
        $state = Get-CollectorLoginState
        $summary = "pid=$($state.CollectorPid) alive=$($state.CollectorAlive) login=$($state.LoginState) realreg=$($state.RealRegSucceeded) hwnd_ready=$($state.NativeHandleReady) registered=$($state.RegisteredCount) realdata=$($state.RealData)"
        if ($summary -ne $lastState) {
            Write-Host "OPENAPI_WAIT $summary"
            $lastState = $summary
        }
        if (-not $state.CollectorAlive -and $state.CollectorPid -gt 0) {
            Write-Warning "Collector exited before OpenAPI became ready."
            return $false
        }
        if (
            $state.CollectorAlive -and
            $state.ProviderStarted -and
            $state.LoginState -eq "connected" -and
            $state.RealRegSucceeded -and
            $state.RegisteredCount -gt 0
        ) {
            Write-Host "COLLECTOR_OPENAPI_READY=True pid=$($state.CollectorPid) registered_count=$($state.RegisteredCount) hwnd=$($state.NativeHwnd)"
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Warning "OpenAPI login/SetRealReg was not confirmed within ${TimeoutSec}s. The login helper was left untouched."
    Report-OpstarterState "login_timeout_no_force_kill"
    return $false
}

function Build-Universe([string]$Python64) {
    Write-Step "Building v2 universe"
    & $Python64 (Join-Path $ProjectRoot "realtime_v2\build_universe.py") --limit 300 --rank-basis today
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) { return }
    throw "build_universe failed with exit code $exitCode; live build and validated cached fallback are both unavailable"
}

function Start-VerifiedContextWriter {
    if (-not (Test-Path -LiteralPath $ContextLauncher)) {
        throw "Verified context launcher not found: $ContextLauncher"
    }
    Write-Step "Starting verified portable v2 context writer"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ContextLauncher
    if ($LASTEXITCODE -ne 0) {
        throw "Verified portable v2 context writer failed with exit code $LASTEXITCODE"
    }
}

function Start-V2([bool]$FastOpen) {
    Ensure-RuntimeDir
    $python64 = Resolve-Python64
    Assert-Python32
    Write-Host "COLLECTOR_PATH=verified_provider_thread_restore"
    Write-Host "PYTHON64=$python64"
    Write-Host "PYTHON64_BITS=$(Get-PythonBits $python64)"
    Write-Host "PYTHON32=$Python32"
    Write-Host "PYTHON32_BITS=$(Get-PythonBits $Python32)"
    Build-Universe $python64

    Start-VerifiedContextWriter

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $workerOut = Join-Path $RuntimeDir "worker64_large_$stamp.out.log"
    $workerErr = Join-Path $RuntimeDir "worker64_large_$stamp.err.log"
    $collectorOut = Join-Path $RuntimeDir "collector32_large_$stamp.out.log"
    $collectorErr = Join-Path $RuntimeDir "collector32_large_$stamp.err.log"

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

    Write-Step "Starting restored 32-bit OpenAPI collector"
    $collectorLimit = 300
    if ($env:STOCKBOARD_V2_COLLECTOR_LIMIT) {
        $parsedLimit = 0
        if ([int]::TryParse([string]$env:STOCKBOARD_V2_COLLECTOR_LIMIT, [ref]$parsedLimit)) {
            $collectorLimit = [Math]::Max(1, [Math]::Min(300, $parsedLimit))
        }
    }
    $collectorArgs = @(
        "realtime_v2\collector32_large_bidask.py",
        "--limit", [string]$collectorLimit,
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
    Write-Host "COLLECTOR_LIMIT=$collectorLimit"

    $oldHide = $env:STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN
    $env:STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN = "0"
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
    Write-Host "COLLECTOR32_STDOUT=$collectorOut"
    Write-Host "COLLECTOR32_STDERR=$collectorErr"

    Write-Step "Waiting for OpenAPI login and actual SetRealReg"
    if (-not (Wait-CollectorOpenApiReady 180)) {
        Write-Host "---- collector stdout tail ----" -ForegroundColor Yellow
        Get-Content -LiteralPath $collectorOut -Tail 80 -ErrorAction SilentlyContinue
        Write-Host "---- collector stderr tail ----" -ForegroundColor Yellow
        Get-Content -LiteralPath $collectorErr -Tail 80 -ErrorAction SilentlyContinue
        throw "Collector did not reach connected + realreg_succeeded state. It was not force-stopped so the console/logs remain available."
    }

    Write-Step "Starting HTS bridge"
    if (Test-Path -LiteralPath $OldLauncher) {
        & cmd.exe /c "`"$OldLauncher`" ahk"
    }
    Start-Process $BoardUrl
    Write-Host ""
    Write-Host "Open $BoardUrl"
}

function Get-ContextOwnerStatus {
    if (-not (Test-Path -LiteralPath $ContextOwnerStatusFile)) { return $null }
    try {
        return Get-Content -LiteralPath $ContextOwnerStatusFile -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Show-Status {
    Ensure-RuntimeDir
    $state = Get-CollectorLoginState
    $queue = $state.Collector.sender_stats.pending_total_count
    $contextRows = @(Get-ContextWriterRows)
    $contextOwner = Get-ContextOwnerStatus
    [pscustomobject]@{
        CollectorPid = $state.CollectorPid
        CollectorAlive = $state.CollectorAlive
        LoginState = $state.LoginState
        RealRegSucceeded = $state.RealRegSucceeded
        RegisteredCount = $state.RegisteredCount
        NativeHandleReady = $state.NativeHandleReady
        RealData = $state.RealData
        TradeReceived = $state.TradeReceived
        RealDataLastAt = $state.RealDataLastAt
        WorkerTrades = $state.WorkerTrades
        WorkerLastEventAt = $state.WorkerLastEventAt
        Queue = $queue
        ContextWriterCount = $contextRows.Count
        ContextWriterOwnerPid = $contextOwner.context_writer_owner_pid
        ContextWriterOwnerModule = $contextOwner.context_writer_owner_module
        LegacyContextWriterDetected = $contextOwner.legacy_context_writer_detected
        LastError = $state.LastError
    } | Format-List
    Report-OpstarterState "status_only_no_force_kill"
}

function Invoke-Doctor {
    Ensure-RuntimeDir
    $lines = New-Object System.Collections.Generic.List[string]
    function Add-Line([string]$Text) {
        $lines.Add($Text) | Out-Null
        Write-Host $Text
    }
    Add-Line "StockBoard v2 restored collector doctor"
    Add-Line "TIME=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Add-Line "GIT_BRANCH=$(& git -C $ProjectRoot branch --show-current 2>$null)"
    Add-Line "GIT_HEAD=$(& git -C $ProjectRoot rev-parse --short HEAD 2>$null)"
    Add-Line "PYTHON32=$Python32"
    Add-Line "PYTHON32_BITS=$(Get-PythonBits $Python32)"
    $state = Get-CollectorLoginState
    foreach ($name in @(
        "CollectorPid", "CollectorAlive", "LoginState", "RealRegSucceeded",
        "RegisteredCount", "NativeHandleReady", "RealData", "TradeReceived",
        "RealDataLastAt", "WorkerTrades", "WorkerLastEventAt", "LastError"
    )) {
        Add-Line "$name=$($state.$name)"
    }
    $contextRows = @(Get-ContextWriterRows)
    $contextOwner = Get-ContextOwnerStatus
    Add-Line "ContextWriterCount=$($contextRows.Count)"
    Add-Line "ContextWriterOwnerPid=$($contextOwner.context_writer_owner_pid)"
    Add-Line "ContextWriterOwnerModule=$($contextOwner.context_writer_owner_module)"
    Add-Line "LegacyContextWriterDetected=$($contextOwner.legacy_context_writer_detected)"
    foreach ($row in $contextRows) {
        Add-Line "ContextWriter PID=$($row.ProcessId) COMMAND=$($row.CommandLine)"
    }
    foreach ($pattern in @("worker64_large_*.err.log", "collector32_large_*.out.log", "collector32_large_*.err.log")) {
        $file = Get-ChildItem -Path $RuntimeDir -Filter $pattern -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($file) {
            Add-Line "LOG=$($file.FullName)"
            foreach ($line in (Get-Content -LiteralPath $file.FullName -Tail 80 -ErrorAction SilentlyContinue)) {
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
    "restart" { Stop-V2; Start-V2 $false; break }
    "restart-fast" { Stop-V2; Start-V2 $true; break }
    "stop" { Stop-V2; break }
    "status" { Show-Status; break }
    "doctor" { Invoke-Doctor; break }
}
