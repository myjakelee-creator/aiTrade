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
$Top100Url = "http://127.0.0.1:8000/api/top100"
$ProviderStatusUrl = "http://127.0.0.1:8000/api/realtime_provider_status"
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
    return @{
        pid_file = $AhkPidFile
        pid = if ($pidValue -gt 0) { $pidValue } else { $pidText }
        running = $running
        script = $AhkScript
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
    Write-Host "top100_row_count=$($snapshot.top100_row_count)"
    Write-Host "suffix_realreg_requested=$($snapshot.suffix_realreg_requested)"
    Write-Host "suffix_realreg_succeeded=$($snapshot.suffix_realreg_succeeded)"
    Write-Host "AHK_PID_FILE=$($snapshot.ahk.pid_file)"
    Write-Host "AHK_PID=$($snapshot.ahk.pid)"
    Write-Host "AHK_RUNNING=$($snapshot.ahk.running)"
    Write-Host "AHK_SCRIPT=$($snapshot.ahk.script)"
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

function Test-StartupSnapshot {
    param($Snapshot)
    $errors = New-Object System.Collections.Generic.List[string]
    if ($Snapshot.running -ne $true) { $errors.Add("realtime provider running is not true") | Out-Null }
    if ($Snapshot.login_state -ne "connected") { $errors.Add("login_state is '$($Snapshot.login_state)'") | Out-Null }
    if ($Snapshot.suffix_realreg_requested -ne $false) { $errors.Add("suffix_realreg_requested is not false") | Out-Null }
    if ($Snapshot.top100_row_count -lt 160 -or $Snapshot.top100_row_count -gt 220) {
        $errors.Add("top100_row_count is outside 160..220: $($Snapshot.top100_row_count)") | Out-Null
    }
    if ([math]::Abs($Snapshot.registered_count - $Snapshot.top100_row_count) -gt 5) {
        $errors.Add("registered_count differs from top100_row_count: registered=$($Snapshot.registered_count), top100=$($Snapshot.top100_row_count)") | Out-Null
    }
    foreach ($code in $TargetCodes) {
        $row = $Snapshot.target_rows[$code]
        if ($row.stock_code_is_6_digits -ne $true) { $errors.Add("$code stock_code is not 6 digits") | Out-Null }
        if ($row.source -ne $ExpectedSources[$code]) { $errors.Add("$code source is '$($row.source)', expected '$($ExpectedSources[$code])'") | Out-Null }
        if ($row.price_equals_realtime_price -ne $true) { $errors.Add("$code price != realtime_price") | Out-Null }
        if ($row.change_rate_equals_realtime_change_rate -ne $true) { $errors.Add("$code change_rate != realtime_change_rate") | Out-Null }
    }
    return @($errors)
}

function Test-ServerBasicSnapshot {
    param($Snapshot)
    $errors = New-Object System.Collections.Generic.List[string]
    if ($Snapshot.listen_pids.Count -eq 0) { $errors.Add("8000 listener was not found") | Out-Null }
    if ($Snapshot.top100_row_count -lt 160 -or $Snapshot.top100_row_count -gt 220) {
        $errors.Add("top100_row_count is outside 160..220: $($Snapshot.top100_row_count)") | Out-Null
    }
    return @($errors)
}

function Test-ConnectedRealtimeRegistrationSnapshot {
    param($Snapshot)
    $errors = New-Object System.Collections.Generic.List[string]
    if ($Snapshot.running -ne $true) { $errors.Add("realtime provider running is not true") | Out-Null }
    if ($Snapshot.login_state -ne "connected") { $errors.Add("login_state is '$($Snapshot.login_state)'") | Out-Null }
    if ($Snapshot.suffix_realreg_requested -ne $false) { $errors.Add("suffix_realreg_requested is not false") | Out-Null }
    if ([math]::Abs($Snapshot.registered_count - $Snapshot.top100_row_count) -gt 5) {
        $errors.Add("registered_count differs from top100_row_count: registered=$($Snapshot.registered_count), top100=$($Snapshot.top100_row_count)") | Out-Null
    }
    foreach ($code in $TargetCodes) {
        $row = $Snapshot.target_rows[$code]
        if ($row.stock_code_is_6_digits -ne $true) { $errors.Add("$code stock_code is not 6 digits") | Out-Null }
        if ($row.source -ne $ExpectedSources[$code]) { $errors.Add("$code source is '$($row.source)', expected '$($ExpectedSources[$code])'") | Out-Null }
    }
    return @($errors)
}

function Test-RealtimePriceSnapshot {
    param($Snapshot)
    $errors = New-Object System.Collections.Generic.List[string]
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
    if (-not (Test-Path -LiteralPath $AhkPidFile)) {
        Write-Host "AHK bridge PID file not found: $AhkPidFile" -ForegroundColor Yellow
        return
    }
    $rawPid = [string](Get-Content -LiteralPath $AhkPidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $storedPid = 0
    if (-not [int]::TryParse($rawPid, [ref]$storedPid)) {
        Write-Host "AHK bridge PID file is invalid: $AhkPidFile" -ForegroundColor Yellow
        return
    }
    try {
        $process = Get-Process -Id $storedPid -ErrorAction Stop
    } catch {
        Write-Host "AHK bridge PID is not running: $storedPid" -ForegroundColor Yellow
        return
    }
    if ($process.ProcessName -notlike "AutoHotkey*") {
        Write-Host "PID $storedPid is not an AutoHotkey process. AHK bridge was not stopped." -ForegroundColor Yellow
        return
    }
    Write-Host "Stopping AHK bridge PID: $storedPid"
    try {
        Stop-Process -Id $storedPid -Force -ErrorAction Stop
        Remove-Item -LiteralPath $AhkPidFile -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Host "Could not stop AHK bridge PID $storedPid`: $($_.Exception.Message)" -ForegroundColor Yellow
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

    $serverBasicReady = $false
    $snapshot = $null
    $lastError = ""
    $deadline = (Get-Date).AddMinutes(6)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $process.Refresh()
        if ($process.HasExited) {
            $lastError = "server process exited with code $($process.ExitCode)"
            break
        }
        try {
            $snapshot = Write-Status
            if ($snapshot.x_stockboard_pid -and ([string]$snapshot.x_stockboard_pid -ne [string]$process.Id)) {
                $lastError = "X-StockBoard-PID '$($snapshot.x_stockboard_pid)' did not match new PID '$($process.Id)'"
                continue
            }
            $basicErrors = Test-ServerBasicSnapshot $snapshot
            if ($basicErrors.Count -eq 0) {
                $serverBasicReady = $true
                break
            }
            $lastError = $basicErrors -join "; "
        } catch {
            $lastError = $_.Exception.Message
        }
    }

    if (-not $serverBasicReady) {
        Write-StartupValidationFailure "Startup validation failed: server did not become ready." @($lastError) $stdout $stderr
        exit 1
    }

    Write-Host ""
    Write-Host "Server basic startup succeeded."

    $loginConnected = $false
    $deadline = (Get-Date).AddMinutes(6)
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
        Write-Host ""
        Write-Host "OpenAPI login not completed yet" -ForegroundColor Yellow
        Write-Host "STDOUT_LOG=$stdout"
        Write-Host "STDERR_LOG=$stderr"
        exit 2
    }

    $snapshot = Write-Status
    $errors = Test-ConnectedRealtimeRegistrationSnapshot $snapshot
    if ($errors.Count -gt 0) {
        Write-StartupValidationFailure "Startup validation failed after OpenAPI login." $errors $stdout $stderr
        exit 1
    }

    $errors = Test-RealtimePriceSnapshot $snapshot
    if ($errors.Count -gt 0) {
        Write-StartupValidationFailure "Startup realtime price validation failed." $errors $stdout $stderr
        exit 1
    }

    Start-Process $BoardUrl | Out-Null
    $ahkStarted = Start-StockBoardAhkBridge
    if ($ahkStarted) {
        Write-Host "StockBoard started."
    } else {
        Write-Host "StockBoard started with warning: AHK bridge was not started." -ForegroundColor Yellow
        exit 2
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
    "stop" { Stop-StockBoard }
    "restart" { Stop-StockBoard; Start-StockBoard }
    "status" { Write-Step "StockBoard status"; [void](Write-Status) }
    default {
        Write-Host "Usage: stockboard_live.cmd [start|stop|restart|status|ahk|link]"
        exit 1
    }
}
