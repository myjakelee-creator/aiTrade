@echo off
setlocal EnableExtensions
cd /d C:\aiTrade

set "ACTION=%~1"
if "%ACTION%"=="" goto menu
goto run

:menu
echo.
echo StockBoard Live
echo.
echo   1 Start
echo   2 Stop
echo   3 Restart
echo   4 Status
echo   5 Start HTS Bridge only
echo   0 Exit
echo.
set /p "CHOICE=Select: "
if "%CHOICE%"=="1" set "ACTION=start"
if "%CHOICE%"=="2" set "ACTION=stop"
if "%CHOICE%"=="3" set "ACTION=restart"
if "%CHOICE%"=="4" set "ACTION=status"
if "%CHOICE%"=="5" set "ACTION=ahk"
if "%CHOICE%"=="0" exit /b 0
if "%ACTION%"=="" (
  echo Invalid selection.
  goto menu
)

:run
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$raw=Get-Content -LiteralPath '%~f0' -Raw; $marker='# POWERSHELL-BEGIN'; $idx=$raw.LastIndexOf($marker); if($idx -lt 0){throw 'PowerShell marker not found'}; $env:STOCKBOARD_ACTION='%ACTION%'; Invoke-Expression $raw.Substring($idx + $marker.Length)"
if errorlevel 1 (
  echo.
  echo StockBoard command finished with an error or warning. Review the messages above.
  pause
)
exit /b %ERRORLEVEL%

# POWERSHELL-BEGIN
$ErrorActionPreference = "Stop"

$Action = [string]$env:STOCKBOARD_ACTION
$Action = $Action.Trim().ToLowerInvariant()
$ProjectRoot = "C:\aiTrade"
$Python32 = "C:\Users\myjay\AppData\Local\Programs\Python\Python310-32\python.exe"
$EntryPoint = Join-Path $ProjectRoot "kiwoom_trade_value_rank.py"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime"
$PidFile = Join-Path $RuntimeDir "stockboard_server.pid"
$HealthUrl = "http://127.0.0.1:8000/api/health"
$Top100Url = "http://127.0.0.1:8000/api/top100"
$ProviderStatusUrl = "http://127.0.0.1:8000/api/realtime_provider_status"
$RealtimeStatusUrl = "http://127.0.0.1:8000/api/realtime_status"
$BoardUrl = "http://127.0.0.1:8000/"
$AhkScript = Join-Path $ProjectRoot "scripts\stockboard_kiwoom_link_v1.ahk"
$AhkPidFile = Join-Path $RuntimeDir "stockboard_ahk.pid"
$AhkExeCandidates = @(
    "C:\Program Files\AutoHotkey\v1.1.37.02\AutoHotkeyU64.exe",
    "C:\Program Files\AutoHotkey\v1.1.37.02\AutoHotkeyU32.exe",
    "C:\Program Files\AutoHotkey\v1.1.37.02\AutoHotkeyA32.exe",
    "C:\Program Files\AutoHotkey\v1.1\AutoHotkeyU64.exe",
    "C:\Program Files\AutoHotkey\v1.1\AutoHotkeyU32.exe",
    "C:\Program Files\AutoHotkey\AutoHotkey.exe",
    "C:\Program Files (x86)\AutoHotkey\AutoHotkey.exe"
)
$TargetCodes = @("000660", "005930", "402340")
$ExpectedSources = @{
    "000660" = "000660_AL"
    "005930" = "005930_AL"
    "402340" = "402340_AL"
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Ensure-RuntimeDir {
    if (-not (Test-Path -LiteralPath $RuntimeDir)) {
        New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    }
}

function Get-ListenPid8000 {
    $pids = @()
    try {
        $pids = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        $lines = @(netstat -ano -p tcp | Select-String -Pattern "^\s*TCP\s+\S+:8000\s+\S+\s+LISTENING\s+(\d+)\s*$")
        foreach ($line in $lines) {
            $match = [regex]::Match($line.Line, "^\s*TCP\s+\S+:8000\s+\S+\s+LISTENING\s+(\d+)\s*$")
            if ($match.Success) {
                $pids += [int]$match.Groups[1].Value
            }
        }
        $pids = @($pids | Select-Object -Unique)
    }
    return @($pids)
}

function Stop-ListenPid8000Only {
    $listenPids = Get-ListenPid8000
    if ($listenPids.Count -eq 0) {
        Write-Host "No 8000 listener."
    }
    foreach ($listenPid in $listenPids) {
        Write-Host "Stopping 8000 LISTEN PID: $listenPid"
        Stop-Process -Id $listenPid -Force -ErrorAction Stop
    }

    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if ((Get-ListenPid8000).Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Port 8000 was not released."
}

function Get-SourceFallback {
    param($Row)
    foreach ($field in @("realtime_source_code", "source_code", "realtime_registered_code", "registered_code")) {
        if ($Row.PSObject.Properties.Name -contains $field) {
            $value = [string]$Row.$field
            if ($value) {
                return $value
            }
        }
    }
    return $null
}

function Get-Rows {
    param($Response)
    if ($null -eq $Response) {
        return @()
    }
    if ($Response -is [System.Array]) {
        return @($Response)
    }
    $propertyNames = @($Response.PSObject.Properties.Name)
    if ($propertyNames -contains "rows") {
        return @($Response.rows)
    }
    if ($propertyNames -contains "data") {
        return @($Response.data)
    }
    return @($Response)
}

function Invoke-Top100WithHeaders {
    $response = Invoke-WebRequest -Uri $Top100Url -UseBasicParsing -TimeoutSec 10
    return @{
        Response = $response
        Rows = Get-Rows ($response.Content | ConvertFrom-Json)
        HeaderPid = [string]$response.Headers["X-StockBoard-PID"]
    }
}

function Invoke-HealthWithHeaders {
    $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
    return @{
        Response = $response
        Body = ($response.Content | ConvertFrom-Json)
        HeaderPid = [string]$response.Headers["X-StockBoard-PID"]
    }
}

function Resolve-AhkExe {
    foreach ($candidate in $AhkExeCandidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Get-AhkBridgeStatus {
    $pidText = $null
    $pidValue = 0
    $running = "False"
    $bridgePids = @()
    $bridgePidsError = $null
    $scriptName = Split-Path -Leaf $AhkScript
    try {
        $bridgePids = @(Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object {
                $_.Name -like "AutoHotkey*" -and
                $_.CommandLine -and
                $_.CommandLine -like "*$scriptName*"
            } |
            Select-Object -ExpandProperty ProcessId -Unique)
    } catch {
        $bridgePidsError = $_.Exception.Message
    }
    if (Test-Path -LiteralPath $AhkPidFile) {
        $pidText = [string](Get-Content -LiteralPath $AhkPidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ([int]::TryParse($pidText, [ref]$pidValue)) {
            try {
                $process = Get-Process -Id $pidValue -ErrorAction Stop
                if ($process.ProcessName -like "AutoHotkey*") {
                    $running = "True"
                } else {
                    $running = "Unknown"
                }
            } catch {
                $running = "False"
            }
        } else {
            $running = "Unknown"
        }
    }
    if ($bridgePids.Count -gt 0) {
        $running = "True"
    }
    return @{
        pid_file = $AhkPidFile
        pid = if ($pidValue -gt 0) { $pidValue } else { $pidText }
        running = $running
        script = $AhkScript
        bridge_pids = @($bridgePids)
        bridge_pids_error = $bridgePidsError
    }
}

function Get-BasicReadySnapshot {
    param([int]$ExpectedPid)
    $listenPids = Get-ListenPid8000
    $processRunning = $false
    try {
        $serverProcess = Get-Process -Id $ExpectedPid -ErrorAction Stop
        $processRunning = -not $serverProcess.HasExited
    } catch {
        $processRunning = $false
    }

    $health = $null
    $healthError = $null
    $provider = $null
    $providerError = $null
    $realtimeStatus = $null
    $realtimeStatusError = $null
    $endpoint = $null
    $endpointPid = $null
    try {
        $health = Invoke-HealthWithHeaders
        $endpoint = "/api/health"
        $endpointPid = $health.HeaderPid
    } catch {
        $healthError = $_.Exception.Message
    }
    if (-not $endpoint) {
        try {
            $provider = Invoke-RestMethod -Uri $ProviderStatusUrl -TimeoutSec 2
            $endpoint = "/api/realtime_provider_status"
        } catch {
            $providerError = $_.Exception.Message
        }
    }
    if (-not $endpoint) {
        try {
            $realtimeStatus = Invoke-RestMethod -Uri $RealtimeStatusUrl -TimeoutSec 2
            $endpoint = "/api/realtime_status"
        } catch {
            $realtimeStatusError = $_.Exception.Message
        }
    }
    return @{
        listen_pids = @($listenPids)
        expected_pid = $ExpectedPid
        process_running = $processRunning
        endpoint = $endpoint
        endpoint_pid = $endpointPid
        health_ok = ($null -ne $health)
        provider_ok = ($null -ne $provider)
        realtime_status_ok = ($null -ne $realtimeStatus)
        health_error = $healthError
        provider_error = $providerError
        realtime_status_error = $realtimeStatusError
        running = if ($provider -and $null -ne $provider.running) { [bool]$provider.running } else { $null }
        login_state = if ($provider) { $provider.login_state } else { $null }
        registered_count = if ($provider -and $null -ne $provider.registered_count) { [int]$provider.registered_count } else { $null }
        price_fast_mode = if ($provider -and $null -ne $provider.price_fast_mode) { [bool]$provider.price_fast_mode } else { $null }
        orderbook_mode = if ($provider) { $provider.orderbook_mode } else { $null }
        tr_event_connected = if ($provider -and $null -ne $provider.tr_event_connected) { [bool]$provider.tr_event_connected } else { $null }
    }
}

function Get-StatusSnapshot {
    $listenPids = Get-ListenPid8000
    $top = $null
    $provider = $null
    $topError = $null
    $providerError = $null
    try {
        $top = Invoke-Top100WithHeaders
    } catch {
        $topError = $_.Exception.Message
    }
    try {
        $provider = Invoke-RestMethod -Uri $ProviderStatusUrl -TimeoutSec 10
    } catch {
        $providerError = $_.Exception.Message
    }

    $rows = if ($top) { @($top.Rows) } else { @() }
    $targetRows = @{}
    foreach ($code in $TargetCodes) {
        $row = @($rows | Where-Object { $_.stock_code -eq $code } | Select-Object -First 1)
        if ($row.Count -gt 0) {
            $item = $row[0]
            $targetRows[$code] = @{
                source = Get-SourceFallback $item
                price_equals_realtime_price = ($item.price -eq $item.realtime_price)
                change_rate_equals_realtime_change_rate = ($item.change_rate -eq $item.realtime_change_rate)
                price = $item.price
                realtime_price = $item.realtime_price
                change_rate = $item.change_rate
                realtime_change_rate = $item.realtime_change_rate
                stock_code_is_6_digits = ([string]$item.stock_code -match "^\d{6}$")
            }
        } else {
            $targetRows[$code] = @{
                source = $null
                price_equals_realtime_price = $false
                change_rate_equals_realtime_change_rate = $false
                stock_code_is_6_digits = $false
            }
        }
    }

    $ahk = Get-AhkBridgeStatus

    return @{
        listen_pids = @($listenPids)
        x_stockboard_pid = if ($top) { $top.HeaderPid } else { $null }
        running = if ($provider) { [bool]$provider.running } else { $false }
        login_state = if ($provider) { $provider.login_state } else { $null }
        registered_count = if ($provider -and $null -ne $provider.registered_count) { [int]$provider.registered_count } else { 0 }
        realdata_received_count = if ($provider -and $null -ne $provider.realdata_received_count) { [int]$provider.realdata_received_count } else { 0 }
        trade_seen_codes_count = if ($provider -and $null -ne $provider.trade_seen_codes_count) { [int]$provider.trade_seen_codes_count } else { 0 }
        orderbook_seen_codes_count = if ($provider -and $null -ne $provider.orderbook_seen_codes_count) { [int]$provider.orderbook_seen_codes_count } else { 0 }
        tr_event_connected = if ($provider -and $null -ne $provider.tr_event_connected) { [bool]$provider.tr_event_connected } else { $null }
        price_fast_mode = if ($provider -and $null -ne $provider.price_fast_mode) { [bool]$provider.price_fast_mode } else { $false }
        realtime_code_limit = if ($provider -and $null -ne $provider.realtime_code_limit) { [int]$provider.realtime_code_limit } else { 0 }
        orderbook_realtime_enabled = if ($provider -and $null -ne $provider.orderbook_realtime_enabled) { [bool]$provider.orderbook_realtime_enabled } else { $true }
        display_mode = if ($provider) { $provider.display_mode } else { $null }
        orderbook_mode = if ($provider) { $provider.orderbook_mode } else { $null }
        orderbook_hot_source = if ($provider) { $provider.orderbook_hot_source } else { $null }
        orderbook_hot_limit = if ($provider -and $null -ne $provider.orderbook_hot_limit) { [int]$provider.orderbook_hot_limit } else { 0 }
        orderbook_rotate_batch = if ($provider -and $null -ne $provider.orderbook_rotate_batch) { [int]$provider.orderbook_rotate_batch } else { 0 }
        orderbook_rotate_interval_sec = if ($provider -and $null -ne $provider.orderbook_rotate_interval_sec) { [int]$provider.orderbook_rotate_interval_sec } else { 0 }
        orderbook_registered_count = if ($provider -and $null -ne $provider.orderbook_registered_count) { [int]$provider.orderbook_registered_count } else { 0 }
        orderbook_hot_codes_sample = if ($provider) { @($provider.orderbook_hot_codes_sample) } else { @() }
        orderbook_rotate_codes_sample = if ($provider) { @($provider.orderbook_rotate_codes_sample) } else { @() }
        orderbook_last_rotate_at = if ($provider) { $provider.orderbook_last_rotate_at } else { $null }
        strength_5m_enabled = if ($provider -and $null -ne $provider.strength_5m_enabled) { [bool]$provider.strength_5m_enabled } else { $false }
        strength_5m_queue_size = if ($provider -and $null -ne $provider.strength_5m_queue_size) { [int]$provider.strength_5m_queue_size } else { 0 }
        strength_5m_last_cycle_at = if ($provider) { $provider.strength_5m_last_cycle_at } else { $null }
        stale_trade_drop_seconds = if ($provider -and $null -ne $provider.stale_trade_drop_seconds) { [int]$provider.stale_trade_drop_seconds } else { 0 }
        stale_trade_drop_count = if ($provider -and $null -ne $provider.stale_trade_drop_count) { [int]$provider.stale_trade_drop_count } else { 0 }
        last_stale_trade_lag_sec = if ($provider) { $provider.last_stale_trade_lag_sec } else { $null }
        last_trade_lag_sec = if ($provider) { $provider.last_trade_lag_sec } else { $null }
        max_trade_lag_sec = if ($provider) { $provider.max_trade_lag_sec } else { $null }
        avg_trade_lag_sec_recent = if ($provider) { $provider.avg_trade_lag_sec_recent } else { $null }
        top100_row_count = $rows.Count
        suffix_realreg_requested = if ($provider) { [bool]$provider.suffix_realreg_requested } else { $false }
        suffix_realreg_succeeded = if ($provider) { [bool]$provider.suffix_realreg_succeeded } else { $false }
        top100_error = $topError
        provider_error = $providerError
        target_rows = $targetRows
        ahk = $ahk
    }
}

function Write-Status {
    $snapshot = Get-StatusSnapshot
    Write-Host "8000_LISTEN_PID=$($snapshot.listen_pids -join ',')"
    Write-Host "X_STOCKBOARD_PID=$($snapshot.x_stockboard_pid)"
    Write-Host "running=$($snapshot.running)"
    Write-Host "login_state=$($snapshot.login_state)"
    Write-Host "registered_count=$($snapshot.registered_count)"
    Write-Host "realdata_received_count=$($snapshot.realdata_received_count)"
    Write-Host "trade_seen_codes_count=$($snapshot.trade_seen_codes_count)"
    Write-Host "orderbook_seen_codes_count=$($snapshot.orderbook_seen_codes_count)"
    Write-Host "tr_event_connected=$($snapshot.tr_event_connected)"
    Write-Host "display_mode=$($snapshot.display_mode)"
    Write-Host "PRICE_FAST_MODE=$($snapshot.price_fast_mode)"
    Write-Host "REALTIME_CODE_LIMIT=$($snapshot.realtime_code_limit)"
    Write-Host "ENABLE_ORDERBOOK_REALTIME=$($snapshot.orderbook_realtime_enabled)"
    Write-Host "orderbook_mode=$($snapshot.orderbook_mode)"
    Write-Host "orderbook_hot_source=$($snapshot.orderbook_hot_source)"
    Write-Host "orderbook_hot_limit=$($snapshot.orderbook_hot_limit)"
    Write-Host "orderbook_rotate_batch=$($snapshot.orderbook_rotate_batch)"
    Write-Host "orderbook_rotate_interval_sec=$($snapshot.orderbook_rotate_interval_sec)"
    Write-Host "orderbook_registered_count=$($snapshot.orderbook_registered_count)"
    Write-Host "orderbook_hot_codes_sample=$($snapshot.orderbook_hot_codes_sample -join ',')"
    Write-Host "orderbook_rotate_codes_sample=$($snapshot.orderbook_rotate_codes_sample -join ',')"
    Write-Host "orderbook_last_rotate_at=$($snapshot.orderbook_last_rotate_at)"
    Write-Host "strength_5m_enabled=$($snapshot.strength_5m_enabled)"
    Write-Host "strength_5m_queue_size=$($snapshot.strength_5m_queue_size)"
    Write-Host "strength_5m_last_cycle_at=$($snapshot.strength_5m_last_cycle_at)"
    Write-Host "DROP_STALE_TRADE_SECONDS=$($snapshot.stale_trade_drop_seconds)"
    Write-Host "stale_trade_drop_count=$($snapshot.stale_trade_drop_count)"
    Write-Host "last_stale_trade_lag_sec=$($snapshot.last_stale_trade_lag_sec)"
    Write-Host "last_trade_lag_sec=$($snapshot.last_trade_lag_sec)"
    Write-Host "max_trade_lag_sec=$($snapshot.max_trade_lag_sec)"
    Write-Host "avg_trade_lag_sec_recent=$($snapshot.avg_trade_lag_sec_recent)"
    Write-Host "top100_row_count=$($snapshot.top100_row_count)"
    Write-Host "suffix_realreg_requested=$($snapshot.suffix_realreg_requested)"
    Write-Host "suffix_realreg_succeeded=$($snapshot.suffix_realreg_succeeded)"
    Write-Host "AHK_PID_FILE=$($snapshot.ahk.pid_file)"
    Write-Host "AHK_PID=$($snapshot.ahk.pid)"
    Write-Host "AHK_RUNNING=$($snapshot.ahk.running)"
    Write-Host "AHK_SCRIPT=$($snapshot.ahk.script)"
    Write-Host "AHK_BRIDGE_PIDS=$($snapshot.ahk.bridge_pids -join ',')"
    if ($snapshot.ahk.bridge_pids_error) {
        Write-Host "AHK_BRIDGE_PIDS_WARNING=$($snapshot.ahk.bridge_pids_error)" -ForegroundColor Yellow
    }
    if ($snapshot.top100_error) {
        Write-Host "top100_error=$($snapshot.top100_error)" -ForegroundColor Yellow
    }
    if ($snapshot.provider_error) {
        Write-Host "provider_error=$($snapshot.provider_error)" -ForegroundColor Yellow
    }
    foreach ($code in $TargetCodes) {
        $row = $snapshot.target_rows[$code]
        Write-Host ("{0}_source={1}" -f $code, $row.source)
        Write-Host ("{0}_price_equals_realtime_price={1}" -f $code, $row.price_equals_realtime_price)
        Write-Host ("{0}_change_rate_equals_realtime_change_rate={1}" -f $code, $row.change_rate_equals_realtime_change_rate)
    }
    return $snapshot
}

function Test-ServerBasicSnapshot {
    param($Snapshot)
    $errors = New-Object System.Collections.Generic.List[string]
    if ($Snapshot.listen_pids.Count -eq 0) { $errors.Add("8000 listener was not found") | Out-Null }
    if ($Snapshot.process_running -ne $true) { $errors.Add("server process is not running") | Out-Null }
    if (-not $Snapshot.endpoint) { $errors.Add("no lightweight HTTP endpoint responded") | Out-Null }
    if ($Snapshot.endpoint_pid -and ([string]$Snapshot.endpoint_pid -ne [string]$Snapshot.expected_pid)) {
        $errors.Add("health endpoint PID '$($Snapshot.endpoint_pid)' did not match new PID '$($Snapshot.expected_pid)'") | Out-Null
    }
    return @($errors)
}

function Test-ConnectedRealtimeRegistrationSnapshot {
    param($Snapshot)
    $errors = New-Object System.Collections.Generic.List[string]
    if ($Snapshot.running -ne $true) { $errors.Add("realtime provider running is not true") | Out-Null }
    if ($Snapshot.login_state -ne "connected") { $errors.Add("login_state is '$($Snapshot.login_state)'") | Out-Null }
    if ($Snapshot.price_fast_mode -ne $true) { $errors.Add("price_fast_mode is not true") | Out-Null }
    if ($Snapshot.orderbook_mode -ne "hybrid") { $errors.Add("orderbook_mode is '$($Snapshot.orderbook_mode)'") | Out-Null }
    if ($Snapshot.tr_event_connected -ne $true) { $errors.Add("tr_event_connected is not true") | Out-Null }
    if ($Snapshot.suffix_realreg_requested -ne $false) { $errors.Add("suffix_realreg_requested is not false") | Out-Null }
    if ($Snapshot.realtime_code_limit -gt 0) {
        if ([math]::Abs($Snapshot.registered_count - $Snapshot.realtime_code_limit) -gt 5) {
            $errors.Add("registered_count differs from realtime_code_limit: registered=$($Snapshot.registered_count), limit=$($Snapshot.realtime_code_limit)") | Out-Null
        }
    } elseif ($Snapshot.top100_row_count -gt 0 -and [math]::Abs($Snapshot.registered_count - $Snapshot.top100_row_count) -gt 5) {
        $errors.Add("registered_count differs from top100_row_count: registered=$($Snapshot.registered_count), top100=$($Snapshot.top100_row_count)") | Out-Null
    }
    if ($Snapshot.realdata_received_count -gt 0) {
        foreach ($code in $TargetCodes) {
            $row = $Snapshot.target_rows[$code]
            if ($row.stock_code_is_6_digits -ne $true) { $errors.Add("$code stock_code is not 6 digits") | Out-Null }
            if ($row.source -ne $ExpectedSources[$code]) { $errors.Add("$code source is '$($row.source)', expected '$($ExpectedSources[$code])'") | Out-Null }
        }
    } else {
        $errors.Add("realtime source check skipped: realdata_received_count=0") | Out-Null
    }
    return @($errors)
}

function Test-RealtimePriceSnapshot {
    param($Snapshot)
    $errors = New-Object System.Collections.Generic.List[string]
    if ($Snapshot.realdata_received_count -le 0) {
        $errors.Add("realtime price equality skipped: realdata_received_count=0") | Out-Null
        return @($errors)
    }
    foreach ($code in $TargetCodes) {
        $row = $Snapshot.target_rows[$code]
        if ($row.price_equals_realtime_price -ne $true) { $errors.Add("$code price != realtime_price") | Out-Null }
    }
    return @($errors)
}

function Write-StartupValidationFailure {
    param(
        [string]$Title,
        [string[]]$Errors,
        [string]$Stdout,
        [string]$Stderr
    )
    Write-Host ""
    Write-Host $Title -ForegroundColor Red
    foreach ($errorMessage in $Errors) {
        Write-Host " - $errorMessage" -ForegroundColor Red
    }
    Write-Host "STDOUT_LOG=$Stdout"
    Write-Host "STDERR_LOG=$Stderr"
}

function Stop-StockBoardAhkBridge {
    Ensure-RuntimeDir
    $scriptName = Split-Path -Leaf $AhkScript
    $escapedScriptName = $scriptName.Replace("'", "''")
    $escapedPidFile = $AhkPidFile.Replace("'", "''")
    $cleanupScript = @"
`$ErrorActionPreference = 'Continue'
`$scriptName = '$escapedScriptName'
`$pidFile = '$escapedPidFile'
`$ids = New-Object System.Collections.Generic.HashSet[int]
try {
    Get-CimInstance Win32_Process |
        Where-Object { `$_.Name -like 'AutoHotkey*' -and `$_.CommandLine -and `$_.CommandLine -like "*`$scriptName*" } |
        ForEach-Object { [void]`$ids.Add([int]`$_.ProcessId) }
} catch {
    Write-Host "AHK bridge command line lookup failed: `$(`$_.Exception.Message)"
}
if (Test-Path -LiteralPath `$pidFile) {
    `$rawPid = [string](Get-Content -LiteralPath `$pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    `$storedPid = 0
    if ([int]::TryParse(`$rawPid, [ref]`$storedPid)) {
        try {
            `$process = Get-Process -Id `$storedPid -ErrorAction Stop
            if (`$process.ProcessName -like 'AutoHotkey*') {
                [void]`$ids.Add(`$storedPid)
            }
        } catch {}
    }
}
foreach (`$id in `$ids) {
    try {
        Write-Host "Stopping AHK bridge PID: `$id"
        Stop-Process -Id `$id -Force -ErrorAction Stop
    } catch {
        Write-Host "Could not stop AHK bridge PID `${id}: `$(`$_.Exception.Message)"
    }
}
Remove-Item -LiteralPath `$pidFile -Force -ErrorAction SilentlyContinue
Write-Host "AHK bridge cleanup target count: `$(`$ids.Count)"
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cleanupScript))
    try {
        Write-Host "Stopping AHK bridge as administrator. Approve the UAC prompt if Windows asks."
        $process = Start-Process -FilePath "powershell.exe" `
            -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded `
            -Verb RunAs `
            -Wait `
            -PassThru `
            -ErrorAction Stop
        if ($process.ExitCode -ne 0) {
            Write-Host "AHK bridge cleanup exited with code $($process.ExitCode)" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "Could not start elevated AHK bridge cleanup: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

function Start-StockBoardAhkBridge {
    if (-not (Test-Path -LiteralPath $AhkScript)) {
        Write-Host "AHK bridge script not found: $AhkScript" -ForegroundColor Yellow
        return $false
    }
    $ahkExe = Resolve-AhkExe
    if (-not $ahkExe) {
        Write-Host "AutoHotkey v1 executable not found." -ForegroundColor Yellow
        return $false
    }
    try {
        Write-Host "Starting AHK bridge as administrator. Approve the UAC prompt if Windows asks."
        $process = Start-Process -FilePath $ahkExe -ArgumentList "`"$AhkScript`"" -Verb RunAs -PassThru -ErrorAction Stop
        if ($process -and $process.Id) {
            Set-Content -LiteralPath $AhkPidFile -Value ([string]$process.Id) -Encoding ASCII
            Write-Host "AHK_PID=$($process.Id)"
        } else {
            Write-Host "AHK bridge started, but PID was not returned." -ForegroundColor Yellow
        }
        Write-Host "AHK_EXE=$ahkExe"
        Write-Host "AHK_SCRIPT=$AhkScript"
        return $true
    } catch {
        Write-Host "AHK bridge start failed: $($_.Exception.Message)" -ForegroundColor Yellow
        return $false
    }
}

function Start-AhkBridgeOnly {
    Write-Step "Starting HTS Bridge only"
    Ensure-RuntimeDir
    Stop-StockBoardAhkBridge
    $ahkStarted = Start-StockBoardAhkBridge
    if ($ahkStarted) {
        Write-Host "HTS bridge started."
    } else {
        Write-Host "HTS bridge start warning. StockBoard server was not changed." -ForegroundColor Yellow
        exit 2
    }
}

function Start-StockBoard {
    Write-Step "Starting StockBoard"
    Set-Location -LiteralPath $ProjectRoot
    Ensure-RuntimeDir
    if (-not (Test-Path -LiteralPath $Python32)) {
        throw "32-bit Python was not found: $Python32"
    }
    if (-not (Test-Path -LiteralPath $EntryPoint)) {
        throw "Server entry point was not found: $EntryPoint"
    }

    Stop-ListenPid8000Only

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $stdout = Join-Path $RuntimeDir "stockboard_server_$timestamp.out.log"
    $stderr = Join-Path $RuntimeDir "stockboard_server_$timestamp.err.log"

    $env:PYTHONUNBUFFERED = "1"
    $env:STOCKBOARD_ENABLE_COM_REALTIME = "1"
    $env:STOCKBOARD_REGISTER_TOP100_REALTIME = "1"
    $env:STOCKBOARD_PRICE_FAST_MODE = "1"
    $env:STOCKBOARD_REALTIME_CODE_LIMIT = "100"
    $env:STOCKBOARD_ENABLE_ORDERBOOK_REALTIME = "1"
    $env:STOCKBOARD_DISPLAY_MODE = "fast"
    $env:STOCKBOARD_ORDERBOOK_MODE = "hybrid"
    $env:STOCKBOARD_ORDERBOOK_HOT_SOURCE = "top5"
    $env:STOCKBOARD_ORDERBOOK_HOT_LIMIT = "5"
    $env:STOCKBOARD_ORDERBOOK_ROTATE_BATCH = "20"
    $env:STOCKBOARD_ORDERBOOK_ROTATE_INTERVAL_SEC = "5"
    $env:STOCKBOARD_ORDERBOOK_DISPLAY = "numeric"
    $env:STOCKBOARD_STRENGTH_5M_ENABLED = "0"
    $env:STOCKBOARD_DROP_STALE_TRADE_SECONDS = "5"
    Remove-Item Env:\STOCKBOARD_ENABLE_SUFFIX_REALREG_EXPERIMENT -ErrorAction SilentlyContinue
    Remove-Item Env:\STOCKBOARD_ENABLE_DIAGNOSTIC_REALREG -ErrorAction SilentlyContinue
    Remove-Item Env:\STOCKBOARD_ENABLE_EXPECTED_REALREG_EXPERIMENT -ErrorAction SilentlyContinue
    Remove-Item Env:\STOCKBOARD_ENABLE_AFTER_SINGLE_REALREG_EXPERIMENT -ErrorAction SilentlyContinue

    $process = Start-Process -FilePath $Python32 `
        -ArgumentList "-u", ".\kiwoom_trade_value_rank.py" `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    Set-Content -LiteralPath $PidFile -Value ([string]$process.Id) -Encoding ASCII
    Write-Host "SERVER_PID=$($process.Id)"
    Write-Host "STDOUT_LOG=$stdout"
    Write-Host "STDERR_LOG=$stderr"
    Write-Host "PRICE_FAST_MODE=$env:STOCKBOARD_PRICE_FAST_MODE"
    Write-Host "REALTIME_CODE_LIMIT=$env:STOCKBOARD_REALTIME_CODE_LIMIT"
    Write-Host "ENABLE_ORDERBOOK_REALTIME=$env:STOCKBOARD_ENABLE_ORDERBOOK_REALTIME"
    Write-Host "DISPLAY_MODE=$env:STOCKBOARD_DISPLAY_MODE"
    Write-Host "ORDERBOOK_MODE=$env:STOCKBOARD_ORDERBOOK_MODE"
    Write-Host "ORDERBOOK_HOT_SOURCE=$env:STOCKBOARD_ORDERBOOK_HOT_SOURCE"
    Write-Host "ORDERBOOK_HOT_LIMIT=$env:STOCKBOARD_ORDERBOOK_HOT_LIMIT"
    Write-Host "ORDERBOOK_ROTATE_BATCH=$env:STOCKBOARD_ORDERBOOK_ROTATE_BATCH"
    Write-Host "ORDERBOOK_ROTATE_INTERVAL_SEC=$env:STOCKBOARD_ORDERBOOK_ROTATE_INTERVAL_SEC"
    Write-Host "ORDERBOOK_DISPLAY=$env:STOCKBOARD_ORDERBOOK_DISPLAY"
    Write-Host "DROP_STALE_TRADE_SECONDS=$env:STOCKBOARD_DROP_STALE_TRADE_SECONDS"

    $serverBasicReady = $false
    $snapshot = $null
    $lastError = ""
    $basicReadyReason = $null
    $basicWarnings = New-Object System.Collections.Generic.List[string]
    $basicStartedAt = Get-Date
    $deadline = $basicStartedAt.AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) {
            $lastError = "server process exited with code $($process.ExitCode)"
            break
        }
        try {
            $snapshot = Get-BasicReadySnapshot -ExpectedPid $process.Id
            $basicErrors = Test-ServerBasicSnapshot $snapshot
            if ($basicErrors.Count -eq 0) {
                $serverBasicReady = $true
                $basicReadyReason = "server listening and lightweight endpoint responding"
                break
            }
            $lastError = $basicErrors -join "; "
        } catch {
            $lastError = $_.Exception.Message
        }
        $elapsed = [int]((Get-Date) - $basicStartedAt).TotalSeconds
        $listenText = if ($snapshot -and $snapshot.listen_pids) { $snapshot.listen_pids -join "," } else { "" }
        $endpointText = if ($snapshot) { $snapshot.endpoint } else { "" }
        $processAlive = if ($snapshot) { $snapshot.process_running } else { -not $process.HasExited }
        Write-Host ("BASIC_READY_WAIT elapsed={0}s pid_alive={1} listen={2} endpoint={3} last={4}" -f $elapsed, $processAlive, $listenText, $endpointText, $lastError)
        Start-Sleep -Seconds 1
    }

    if (-not $serverBasicReady) {
        Write-Host ""
        Write-Host "Basic ready polling ended; checking final status once..." -ForegroundColor Yellow
        try {
            $fallbackSnapshot = Write-Status
            if ($fallbackSnapshot.running -eq $true) {
                $serverBasicReady = $true
                $basicReadyReason = "provider status running after delayed readiness"
                $basicWarnings.Add("Basic health endpoint did not pass within polling window; final status running=True") | Out-Null
                $snapshot = @{
                    listen_pids = @($fallbackSnapshot.listen_pids)
                    endpoint = "/api/realtime_provider_status"
                }
            }
        } catch {
            $lastError = "$lastError; final status check failed: $($_.Exception.Message)"
        }
    }

    if (-not $serverBasicReady) {
        Write-StartupValidationFailure "Startup validation failed: server did not become ready." @($lastError) $stdout $stderr
        exit 1
    }

    Write-Host ""
    Write-Host "BASIC_READY=True"
    Write-Host "BASIC_READY_REASON=$basicReadyReason"
    if ($basicWarnings.Count -gt 0) {
        Write-Host "BASIC_READY_WARNINGS=$($basicWarnings -join '; ')" -ForegroundColor Yellow
    } else {
        Write-Host "BASIC_READY_WARNINGS="
    }
    Write-Host "SERVER_PID=$($process.Id)"
    Write-Host "8000_LISTEN_PID=$($snapshot.listen_pids -join ',')"
    Write-Host "BASIC_ENDPOINT=$($snapshot.endpoint)"

    $loginConnected = $false
    $strictWarnings = New-Object System.Collections.Generic.List[string]
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        $snapshot = Write-Status
        if ($snapshot.login_state -eq "connected") {
            $loginConnected = $true
            break
        }
        if ($snapshot.login_state -eq "requested") {
            Write-Host "OpenAPI login_state=requested; waiting for login completion..."
        } else {
            Write-Host "OpenAPI login_state=$($snapshot.login_state); waiting for login completion..."
        }
        Start-Sleep -Seconds 5
    }

    if (-not $loginConnected) {
        $strictWarnings.Add("OpenAPI login not completed yet; login_state=$($snapshot.login_state)") | Out-Null
    }

    $snapshot = Write-Status
    $errors = Test-ConnectedRealtimeRegistrationSnapshot $snapshot
    if ($errors.Count -gt 0) {
        foreach ($errorMessage in $errors) {
            $strictWarnings.Add($errorMessage) | Out-Null
        }
    }

    $errors = Test-RealtimePriceSnapshot $snapshot
    if ($errors.Count -gt 0) {
        foreach ($errorMessage in $errors) {
            $strictWarnings.Add($errorMessage) | Out-Null
        }
    }

    Write-Host ""
    Write-Host "STRICT_READY=$($strictWarnings.Count -eq 0)"
    if ($strictWarnings.Count -gt 0) {
        Write-Host "STRICT_WARNINGS=$($strictWarnings -join '; ')" -ForegroundColor Yellow
    } else {
        Write-Host "STRICT_WARNINGS="
    }
    Write-Host "LOGIN_STATE=$($snapshot.login_state)"
    Write-Host "REGISTERED_COUNT=$($snapshot.registered_count)"
    Write-Host "PRICE_FAST_MODE=$($snapshot.price_fast_mode)"
    Write-Host "ORDERBOOK_MODE=$($snapshot.orderbook_mode)"
    Write-Host "TR_EVENT_CONNECTED=$($snapshot.tr_event_connected)"
    Write-Host "STDOUT_LOG=$stdout"
    Write-Host "STDERR_LOG=$stderr"

    Start-Process $BoardUrl | Out-Null
    Stop-StockBoardAhkBridge
    $ahkStarted = Start-StockBoardAhkBridge
    if ($ahkStarted) {
        Write-Host "StockBoard started."
    } else {
        Write-Host "StockBoard started with warning: AHK bridge was not started." -ForegroundColor Yellow
    }
}

function Stop-StockBoard {
    Write-Step "Stopping StockBoard"
    Stop-StockBoardAhkBridge
    Stop-ListenPid8000Only
    if (Test-Path -LiteralPath $PidFile) {
        $rawPid = (Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        $storedPid = 0
        if ([int]::TryParse($rawPid, [ref]$storedPid)) {
            $listenPids = Get-ListenPid8000
            if ($listenPids -contains $storedPid) {
                Stop-Process -Id $storedPid -Force -ErrorAction SilentlyContinue
            }
        }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Browser windows were not closed."
}

switch ($Action) {
    "start" { Start-StockBoard }
    "ahk" { Start-AhkBridgeOnly }
    "link" { Start-AhkBridgeOnly }
    "ahk-stop" { Write-Step "Stopping HTS Bridge only"; Stop-StockBoardAhkBridge }
    "unlink" { Write-Step "Stopping HTS Bridge only"; Stop-StockBoardAhkBridge }
    "ahk-clean" { Write-Step "Stopping HTS Bridge only"; Stop-StockBoardAhkBridge }
    "stop" { Stop-StockBoard }
    "restart" { Stop-StockBoard; Start-StockBoard }
    "status" { Write-Step "StockBoard status"; [void](Write-Status) }
    default {
        Write-Host "Usage: stockboard_live.cmd [start|stop|restart|status|ahk|link|ahk-stop|unlink|ahk-clean]"
        exit 1
    }
}
