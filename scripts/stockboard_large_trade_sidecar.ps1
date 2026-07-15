param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$PidFile = Join-Path $RuntimeDir "large_trade_sidecar.pid"
$StatusFile = Join-Path $RuntimeDir "large_trade_sidecar_status.json"
$CodesFile = Join-Path $RuntimeDir "codes.txt"
$ScriptPath = Join-Path $ProjectRoot "realtime_v2\large_trade_collector32.py"
$SnapshotUrl = "http://127.0.0.1:8765/api/v2/snapshot?limit=1"

function Ensure-RuntimeDir {
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
}

function Resolve-Python32 {
    $candidates = @()
    if ($env:STOCKBOARD_PYTHON32) { $candidates += [string]$env:STOCKBOARD_PYTHON32 }
    $candidates += @(
        "C:\Users\myjay\AppData\Local\Programs\Python\Python310-32\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310-32\python.exe"
    )
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not $candidate -or -not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            $bits = & $candidate -c "import struct; print(struct.calcsize('P')*8)" 2>$null
            if ([string]$bits -eq "32") { return $candidate }
        } catch { }
    }
    throw "32-bit Python was not found. Set STOCKBOARD_PYTHON32."
}

function Read-Pid {
    if (-not (Test-Path -LiteralPath $PidFile)) { return 0 }
    $raw = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $number = 0
    if ([int]::TryParse([string]$raw, [ref]$number)) { return $number }
    return 0
}

function Test-Alive([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-PriceCollectorState {
    try {
        $snapshot = Invoke-RestMethod -Uri $SnapshotUrl -TimeoutSec 3
        $provider = $snapshot.status.collector_status.status
        return [pscustomobject]@{
            Available = $true
            LoginState = [string]$provider.login_state
            RealRegSucceeded = [bool]$provider.realreg_succeeded
            RegisteredCount = [int]$provider.realreg_code_count
            RealDataCount = [int]$provider.realdata_received_count
            LastError = [string]$provider.last_error
        }
    } catch {
        return [pscustomobject]@{
            Available = $false
            LoginState = "unavailable"
            RealRegSucceeded = $false
            RegisteredCount = 0
            RealDataCount = 0
            LastError = $_.Exception.Message
        }
    }
}

function Test-PriceCollectorReady($State) {
    return (
        $null -ne $State -and
        $State.Available -and
        $State.LoginState -eq "connected" -and
        $State.RealRegSucceeded -and
        $State.RegisteredCount -gt 0 -and
        -not $State.LastError
    )
}

function Stop-Sidecar {
    Ensure-RuntimeDir
    $processId = Read-Pid
    if (Test-Alive $processId) {
        Write-Host "Stopping large-trade sidecar PID=$processId"
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Show-Status {
    Ensure-RuntimeDir
    $processId = Read-Pid
    Write-Host "LARGE_TRADE_SIDECAR_PID=$processId"
    Write-Host "LARGE_TRADE_SIDECAR_ALIVE=$(Test-Alive $processId)"
    if (Test-Path -LiteralPath $StatusFile) {
        try {
            $status = Get-Content -LiteralPath $StatusFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $properties = @(
                "mode",
                "pid",
                "running",
                "login_state",
                "registered_code_count",
                "registered_limit",
                "realdata_received_count",
                "fid15_read_count",
                "large_trade_event_count",
                "large_trade_buy_count",
                "large_trade_sell_count",
                "last_large_trade_code",
                "last_large_trade_at",
                "last_error",
                "updated_at"
            )
            $status | Select-Object -Property $properties | Format-List
        } catch {
            Write-Warning "Sidecar status read failed: $($_.Exception.Message)"
        }
    } else {
        Write-Host "LARGE_TRADE_SIDECAR_STATUS=missing"
    }
    $price = Get-PriceCollectorState
    Write-Host "PRICE_COLLECTOR_READY=$(Test-PriceCollectorReady $price)"
    Write-Host "PRICE_COLLECTOR_LOGIN=$($price.LoginState)"
    Write-Host "PRICE_COLLECTOR_REGISTERED=$($price.RegisteredCount)"
    Write-Host "PRICE_COLLECTOR_REALDATA=$($price.RealDataCount)"
    Write-Host "PRICE_COLLECTOR_ERROR=$($price.LastError)"
}

function Start-Sidecar {
    Ensure-RuntimeDir
    Stop-Sidecar

    $enabled = if ($null -eq $env:STOCKBOARD_LARGE_TRADE_SIDECAR_ENABLED) {
        "1"
    } else {
        [string]$env:STOCKBOARD_LARGE_TRADE_SIDECAR_ENABLED
    }
    if ($enabled -notin @("1", "true", "TRUE", "on", "ON")) {
        Write-Host "LARGE_TRADE_SIDECAR_ENABLED=False"
        return
    }
    if (-not (Test-Path -LiteralPath $ScriptPath)) {
        throw "Sidecar script not found: $ScriptPath"
    }
    if (-not (Test-Path -LiteralPath $CodesFile)) {
        throw "Sidecar codes file not found: $CodesFile"
    }

    $priceBefore = Get-PriceCollectorState
    if (-not (Test-PriceCollectorReady $priceBefore)) {
        Write-Warning "Price collector is not ready; large-trade sidecar startup skipped."
        return
    }

    $python32 = Resolve-Python32
    $normalLimit = 100
    if ($env:STOCKBOARD_V2_COLLECTOR_LIMIT) {
        $parsed = 0
        if ([int]::TryParse([string]$env:STOCKBOARD_V2_COLLECTOR_LIMIT, [ref]$parsed)) {
            $normalLimit = [Math]::Max(1, [Math]::Min(100, $parsed))
        }
    }
    $openingLimit = 20
    if ($env:STOCKBOARD_LARGE_TRADE_OPENING_LIMIT) {
        $parsedOpening = 0
        if ([int]::TryParse([string]$env:STOCKBOARD_LARGE_TRADE_OPENING_LIMIT, [ref]$parsedOpening)) {
            $openingLimit = [Math]::Max(1, [Math]::Min($normalLimit, $parsedOpening))
        }
    }

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $stdout = Join-Path $RuntimeDir "large_trade_sidecar_$stamp.out.log"
    $stderr = Join-Path $RuntimeDir "large_trade_sidecar_$stamp.err.log"
    $arguments = @(
        "realtime_v2\large_trade_collector32.py",
        "--codes-file", $CodesFile,
        "--limit", [string]$normalLimit,
        "--opening-limit", [string]$openingLimit,
        "--suffix", "AL",
        "--flush-ms", "50"
    )

    $process = Start-Process `
        -FilePath $python32 `
        -ArgumentList $arguments `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Minimized `
        -PassThru
    Set-Content -LiteralPath $PidFile -Value $process.Id -Encoding ASCII
    Write-Host "LARGE_TRADE_SIDECAR_PID=$($process.Id)"
    Write-Host "LARGE_TRADE_SIDECAR_OPENING_LIMIT=$openingLimit"
    Write-Host "LARGE_TRADE_SIDECAR_NORMAL_LIMIT=$normalLimit"
    Write-Host "LARGE_TRADE_SIDECAR_STDOUT=$stdout"
    Write-Host "LARGE_TRADE_SIDECAR_STDERR=$stderr"

    Start-Sleep -Seconds 5
    if (-not (Test-Alive $process.Id)) {
        Write-Warning "Large-trade sidecar exited during startup. Price collector remains independent."
        Get-Content -LiteralPath $stderr -Tail 40 -ErrorAction SilentlyContinue
        return
    }

    $priceAfter = Get-PriceCollectorState
    if (-not (Test-PriceCollectorReady $priceAfter)) {
        Write-Warning "Price collector readiness changed after sidecar startup; sidecar is being stopped."
        Stop-Sidecar
        return
    }
    Write-Host "PRICE_COLLECTOR_GUARD=passed"
}

Set-Location -LiteralPath $ProjectRoot
switch ($Action) {
    "start" { Start-Sidecar }
    "stop" { Stop-Sidecar }
    "status" { Show-Status }
}
