[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "restart", "status", "publish", "unpublish", "stop")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$LegacyEntry = Join-Path $ProjectRoot "scripts\stockboard_public_gateway_entry.ps1"
$GatewayPidFile = Join-Path $RuntimeDir "public_gateway.pid"
$GatewayPort = 8766
$GatewayBaseUrl = "http://127.0.0.1:$GatewayPort"
$GatewayHealthUrl = "$GatewayBaseUrl/api/v2/health"
$GatewaySnapshotUrl = "$GatewayBaseUrl/api/v2/snapshot?limit=1"
$PrivateHealthUrl = "http://127.0.0.1:8765/api/v2/health"
$PrivateTarget = "http://127.0.0.1:8765"
$ExpectedVersion = "stockboard_public_current_ui_v2_20260721"
$ExpectedUiMarker = "STOCKBOARD_PUBLIC_CURRENT_UI_V2_20260721"

Set-Location -LiteralPath $ProjectRoot

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Invoke-LegacyEntry([string]$LegacyAction, [switch]$AllowFailure) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $LegacyEntry -Action $LegacyAction
    $exitCode = $LASTEXITCODE
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "Legacy public gateway action '$LegacyAction' failed with exit code $exitCode."
    }
    return $exitCode
}

function Resolve-TailscaleExe {
    $command = Get-Command "tailscale.exe" -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        return [string]$command.Source
    }
    foreach ($candidate in @(
        "C:\Program Files\Tailscale\tailscale.exe",
        "C:\Program Files (x86)\Tailscale\tailscale.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Invoke-TailscaleText {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $output = & $Exe @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "tailscale $($Arguments -join ' ') failed with exit code ${exitCode}: $text"
    }
    return [pscustomobject]@{ ExitCode = $exitCode; Text = $text }
}

function Test-PrivateHealth {
    try {
        Invoke-RestMethod -Uri $PrivateHealthUrl -TimeoutSec 5 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Disable-PublicFunnelBeforeReplacement {
    $exe = Resolve-TailscaleExe
    if (-not $exe) {
        return
    }
    $status = Invoke-TailscaleText -Exe $exe -Arguments @("funnel", "status") -AllowFailure
    if ([string]$status.Text -match "127\.0\.0\.1:8766") {
        Write-Step "Disabling stale public Funnel before gateway replacement"
        [void](Invoke-TailscaleText -Exe $exe -Arguments @("funnel", "--https=443", "off") -AllowFailure)
        $status = Invoke-TailscaleText -Exe $exe -Arguments @("funnel", "status") -AllowFailure
        if ([string]$status.Text -match "127\.0\.0\.1:8766") {
            [void](Invoke-TailscaleText -Exe $exe -Arguments @("funnel", "reset") -AllowFailure)
        }
    }
    if (Test-PrivateHealth) {
        [void](Invoke-TailscaleText -Exe $exe -Arguments @("serve", "--bg", $PrivateTarget) -AllowFailure)
    }
}

function Get-GatewayListenerRows {
    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($line in @(netstat -ano -p tcp 2>$null)) {
        $match = [regex]::Match(
            [string]$line,
            '^\s*TCP\s+(\S+):(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$'
        )
        if (-not $match.Success) {
            continue
        }
        $portNumber = 0
        $processId = 0
        if (-not [int]::TryParse($match.Groups[2].Value, [ref]$portNumber)) {
            continue
        }
        if (-not [int]::TryParse($match.Groups[3].Value, [ref]$processId)) {
            continue
        }
        if ($portNumber -ne $GatewayPort) {
            continue
        }
        $rows.Add([pscustomobject]@{
            LocalAddress = [string]$match.Groups[1].Value
            LocalPort = $portNumber
            OwningProcess = $processId
        })
    }
    return $rows.ToArray()
}

function Get-ProcessCommandLine([int]$ProcessId) {
    if ($ProcessId -le 0) {
        return ""
    }
    try {
        return [string](
            Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        ).CommandLine
    } catch {
        return ""
    }
}

function Test-IsPublicGatewayProcess([int]$ProcessId) {
    $commandLine = Get-ProcessCommandLine $ProcessId
    return (
        $commandLine -match '(?i)realtime_v2[\\/]public_gateway\.py' -or
        $commandLine -match '(?i)realtime_v2[\\/]public_gateway_core\.py'
    )
}

function Stop-StalePublicGateway {
    Disable-PublicFunnelBeforeReplacement
    [void](Invoke-LegacyEntry -LegacyAction "stop" -AllowFailure)

    $deadline = (Get-Date).AddSeconds(10)
    do {
        $listeners = @(Get-GatewayListenerRows)
        if ($listeners.Count -eq 0) {
            break
        }
        foreach ($listener in $listeners) {
            $pidNumber = [int]$listener.OwningProcess
            if (-not (Test-IsPublicGatewayProcess $pidNumber)) {
                $commandLine = Get-ProcessCommandLine $pidNumber
                throw "Port $GatewayPort is owned by an unconfirmed process. PID=$pidNumber COMMAND=$commandLine"
            }
            Write-Host "Stopping stale public gateway PID=$pidNumber"
            Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)

    $remaining = @(Get-GatewayListenerRows)
    if ($remaining.Count -gt 0) {
        throw "Port $GatewayPort still has a listener after the forced gateway replacement."
    }
    Remove-Item -LiteralPath $GatewayPidFile -Force -ErrorAction SilentlyContinue
}

function Wait-GatewayReady([int]$TimeoutSec = 25) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        try {
            $health = Invoke-RestMethod -Uri $GatewayHealthUrl -TimeoutSec 5
            if ([bool]$health.ok) {
                return $health
            }
        } catch { }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Assert-CurrentUiGateway {
    $health = Wait-GatewayReady 25
    if (-not $health) {
        throw "Public gateway did not become healthy at $GatewayHealthUrl."
    }
    if ([string]$health.gateway_version -ne $ExpectedVersion) {
        throw "Stale public gateway detected. Expected=$ExpectedVersion Actual=$($health.gateway_version)"
    }
    if ([string]$health.ui_source -ne "live_private_worker_html") {
        throw "Public gateway is not using the current private Worker HTML. ui_source=$($health.ui_source)"
    }

    $response = Invoke-WebRequest -Uri "$GatewayBaseUrl/?gateway_version=$ExpectedVersion" -UseBasicParsing -TimeoutSec 10
    $html = [string]$response.Content
    foreach ($marker in @(
        $ExpectedUiMarker,
        "1분대금",
        "5분강도",
        "공개 읽기 전용 · 현재 UI"
    )) {
        if ($html -notmatch [regex]::Escape($marker)) {
            throw "Current UI marker is missing from port $GatewayPort: $marker"
        }
    }

    $snapshot = Invoke-RestMethod -Uri $GatewaySnapshotUrl -TimeoutSec 5
    if ([string]$snapshot.gateway_version -ne $ExpectedVersion) {
        throw "Snapshot version mismatch. Expected=$ExpectedVersion Actual=$($snapshot.gateway_version)"
    }

    Write-Host "PUBLIC_GATEWAY_VERSION=$ExpectedVersion" -ForegroundColor Green
    Write-Host "PUBLIC_UI_SOURCE=live_private_worker_html"
    Write-Host "PUBLIC_UI_CURRENT_SYNC=True" -ForegroundColor Green
    Write-Host "PUBLIC_UI_HAS_1MIN_VALUE=True"
    Write-Host "PUBLIC_UI_HAS_5MIN_STRENGTH=True"
    Write-Host "PUBLIC_GATEWAY_ROWS=$($snapshot.row_count)"
}

function Start-CurrentUiGateway {
    Write-Step "Replacing any stale public gateway with the current UI gateway"
    Stop-StalePublicGateway
    [void](Invoke-LegacyEntry -LegacyAction "start")
    Assert-CurrentUiGateway
}

try {
    if (-not (Test-Path -LiteralPath $LegacyEntry)) {
        throw "Public gateway entry was not found: $LegacyEntry"
    }

    switch ($Action) {
        "start" {
            Start-CurrentUiGateway
        }
        "restart" {
            Start-CurrentUiGateway
        }
        "publish" {
            Start-CurrentUiGateway
            [void](Invoke-LegacyEntry -LegacyAction "publish")
        }
        "status" {
            [void](Invoke-LegacyEntry -LegacyAction "status")
            if (Test-Path -LiteralPath $GatewayPidFile) {
                try { Assert-CurrentUiGateway } catch { Write-Warning $_.Exception.Message }
            }
        }
        "unpublish" {
            [void](Invoke-LegacyEntry -LegacyAction "unpublish")
        }
        "stop" {
            Stop-StalePublicGateway
            Write-Host "PUBLIC_GATEWAY_RUNNING=False" -ForegroundColor Green
        }
    }
    exit 0
} catch {
    $record = $_
    Write-Host ""
    Write-Host "== StockBoard current UI public gateway error ==" -ForegroundColor Red
    Write-Host "ERROR_MESSAGE=$($record.Exception.Message)" -ForegroundColor Red
    Write-Host "ERROR_SCRIPT=$($record.InvocationInfo.ScriptName)" -ForegroundColor Red
    Write-Host "ERROR_LINE=$($record.InvocationInfo.ScriptLineNumber)" -ForegroundColor Red
    Write-Host "ERROR_POSITION=$($record.InvocationInfo.PositionMessage)" -ForegroundColor Red
    Write-Host "ERROR_STACK=$($record.ScriptStackTrace)" -ForegroundColor Red
    exit 1
}
