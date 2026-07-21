[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "restart", "status", "publish", "unpublish", "stop")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$CoreScript = Join-Path $PSScriptRoot "stockboard_public_gateway.ps1"
$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$PublicUrlFile = Join-Path $RuntimeDir "stockboard_public_url.txt"
$GatewayHealthUrl = "http://127.0.0.1:8766/api/v2/health"
$PrivateHealthUrl = "http://127.0.0.1:8765/api/v2/health"
$PrivateTarget = "http://127.0.0.1:8765"
$GatewayPort = 8766

# Windows PowerShell 5.1 can throw "Argument types do not match" when the
# core launcher's empty Generic.List[object] fallback is wrapped in @(...).
# Provide a predictable command-compatible listener source backed by netstat.
function Get-NetTCPConnection {
    [CmdletBinding()]
    param(
        [int[]]$LocalPort,
        [string[]]$State
    )

    $wantedPorts = @($LocalPort | Where-Object { $_ -gt 0 })
    if ($wantedPorts.Count -eq 0) {
        return
    }

    $listenOnly = @($State) -contains "Listen"
    foreach ($line in @(netstat -ano -p tcp 2>$null)) {
        $match = [regex]::Match(
            [string]$line,
            '^\s*TCP\s+(\S+):(\d+)\s+\S+\s+(\S+)\s+(\d+)\s*$'
        )
        if (-not $match.Success) {
            continue
        }

        $portNumber = 0
        $processId = 0
        if (-not [int]::TryParse($match.Groups[2].Value, [ref]$portNumber)) {
            continue
        }
        if (-not [int]::TryParse($match.Groups[4].Value, [ref]$processId)) {
            continue
        }
        if ($wantedPorts -notcontains $portNumber) {
            continue
        }

        $netState = [string]$match.Groups[3].Value
        if ($listenOnly -and $netState -ne "LISTENING") {
            continue
        }

        [pscustomobject]@{
            LocalAddress = [string]$match.Groups[1].Value
            LocalPort = $portNumber
            OwningProcess = $processId
            State = if ($netState -eq "LISTENING") { "Listen" } else { $netState }
        }
    }
}

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Invoke-CoreAction([string]$CoreAction) {
    & $CoreScript -Action $CoreAction
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
    throw "Tailscale is not installed."
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
    return [pscustomobject]@{
        ExitCode = $exitCode
        Text = $text
    }
}

function Get-TailscaleStatusJson([string]$Exe) {
    $result = Invoke-TailscaleText -Exe $Exe -Arguments @("status", "--json") -AllowFailure
    if ($result.ExitCode -ne 0 -or -not $result.Text) {
        return $null
    }
    try {
        return $result.Text | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Get-TailscaleDnsName($Status) {
    if (-not $Status -or -not $Status.Self) {
        return ""
    }
    return ([string]$Status.Self.DNSName).Trim().TrimEnd(".")
}

function Test-GatewayHealth {
    try {
        $health = Invoke-RestMethod -Uri $GatewayHealthUrl -TimeoutSec 5
        return (
            [bool]$health.ok -and
            [bool]$health.read_only -and
            [bool]$health.upstream_ok -and
            [string]$health.service -eq "stockboard_v2_public_gateway"
        )
    } catch {
        return $false
    }
}

function Test-PrivateHealth {
    try {
        Invoke-RestMethod -Uri $PrivateHealthUrl -TimeoutSec 5 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Get-FunnelStatusText([string]$Exe) {
    return (Invoke-TailscaleText -Exe $Exe -Arguments @("funnel", "status") -AllowFailure).Text
}

function Test-FunnelTargetsGateway([string]$Text) {
    return [string]$Text -match "127\.0\.0\.1:8766"
}

function Disable-Funnel([string]$Exe) {
    [void](Invoke-TailscaleText -Exe $Exe -Arguments @("funnel", "--https=443", "off") -AllowFailure)
    $statusText = Get-FunnelStatusText $Exe
    if (Test-FunnelTargetsGateway $statusText) {
        [void](Invoke-TailscaleText -Exe $Exe -Arguments @("funnel", "reset") -AllowFailure)
    }
}

function Restore-PrivateServe([string]$Exe) {
    if (-not (Test-PrivateHealth)) {
        Write-Warning "Private StockBoard is not healthy; private Serve could not be restored."
        return
    }
    [void](Invoke-TailscaleText -Exe $Exe -Arguments @("serve", "--bg", $PrivateTarget) -AllowFailure)
}

function Wait-FunnelTarget([string]$Exe, [int]$TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        $text = Get-FunnelStatusText $Exe
        if (Test-FunnelTargetsGateway $text) {
            return $true
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Wait-RemoteHealth([string]$Url, [int]$TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        try {
            $health = Invoke-RestMethod -Uri "$Url/api/v2/health" -TimeoutSec 5
            if (
                [bool]$health.ok -and
                [bool]$health.read_only -and
                [string]$health.service -eq "stockboard_v2_public_gateway"
            ) {
                return $true
            }
        } catch { }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Publish-PublicGateway {
    Invoke-CoreAction "start"
    if (-not (Test-GatewayHealth)) {
        throw "The sanitized gateway is not healthy at $GatewayHealthUrl."
    }

    $confirmation = [string]$env:STOCKBOARD_PUBLIC_CONFIRM
    if ($confirmation -cne "PUBLIC") {
        Write-Host ""
        Write-Host "This changes the ts.net address from private Serve to public Funnel." -ForegroundColor Yellow
        Write-Host "Only the sanitized read-only gateway on 127.0.0.1:8766 will be public." -ForegroundColor Yellow
        $confirmation = Read-Host "Type PUBLIC to continue"
    }
    if ($confirmation -cne "PUBLIC") {
        Write-Host "PUBLIC_WEB_ENABLED=False"
        Write-Host "Publishing was cancelled before any network setting changed."
        return
    }

    $exe = Resolve-TailscaleExe
    $status = Get-TailscaleStatusJson $exe
    if (-not $status -or [string]$status.BackendState -ne "Running") {
        throw "Tailscale is not connected."
    }

    try {
        Write-Step "Publishing sanitized gateway with Tailscale Funnel"
        Write-Host "The first Funnel use opens a browser approval page." -ForegroundColor Yellow
        Write-Host "Approve Funnel there; this console will then continue." -ForegroundColor Yellow
        [void](Invoke-TailscaleText -Exe $exe -Arguments @("serve", "--https=443", "off") -AllowFailure)

        # Do not capture this command. Its first-use consent URL and progress must
        # remain visible in Windows PowerShell.
        & $exe funnel --bg ([string]$GatewayPort)
        $funnelExitCode = $LASTEXITCODE
        if ($funnelExitCode -ne 0) {
            Write-Warning "The first Funnel command did not finish. Complete the browser approval."
            [void](Read-Host "After approval, press Enter to retry")
            & $exe funnel --bg ([string]$GatewayPort)
            $funnelExitCode = $LASTEXITCODE
        }
        if ($funnelExitCode -ne 0) {
            throw "tailscale funnel --bg $GatewayPort failed with exit code $funnelExitCode."
        }
        if (-not (Wait-FunnelTarget $exe 60)) {
            throw "Tailscale Funnel did not report target 127.0.0.1:8766."
        }

        $status = Get-TailscaleStatusJson $exe
        $dnsName = Get-TailscaleDnsName $status
        if (-not $dnsName) {
            throw "Tailscale DNS name is unavailable."
        }
        $url = "https://$dnsName"
        New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
        Set-Content -LiteralPath $PublicUrlFile -Value $url -Encoding UTF8

        $remoteOk = Wait-RemoteHealth $url 60
        Write-Host ""
        Write-Host "PUBLIC_WEB_ENABLED=True" -ForegroundColor Green
        Write-Host "PUBLIC_URL=$url" -ForegroundColor Green
        Write-Host "PUBLIC_TARGET=http://127.0.0.1:$GatewayPort"
        Write-Host "REMOTE_HEALTH_OK=$remoteOk"
        Write-Host "PUBLIC_DATA_POLICY=allowlist_read_only"
        if (-not $remoteOk) {
            Write-Warning "Funnel is configured, but public DNS can take up to 10 minutes to propagate."
            Write-Warning "Run status again and retry the URL without Tailscale after DNS propagation."
        }
        Start-Process $url | Out-Null
    } catch {
        $message = $_.Exception.Message
        Remove-Item -LiteralPath $PublicUrlFile -Force -ErrorAction SilentlyContinue
        try { Disable-Funnel $exe } catch { Write-Warning $_.Exception.Message }
        try { Restore-PrivateServe $exe } catch { Write-Warning $_.Exception.Message }
        throw "Public publishing failed. Funnel was disabled and private Serve rollback was attempted. Detail: $message"
    }
}

if (-not (Test-Path -LiteralPath $CoreScript)) {
    Write-Host "ERROR_MESSAGE=Public gateway core script was not found." -ForegroundColor Red
    Write-Host "ERROR_SCRIPT=$CoreScript" -ForegroundColor Red
    exit 1
}

try {
    switch ($Action) {
        "restart" {
            Invoke-CoreAction "stop"
            Invoke-CoreAction "start"
        }
        "publish" {
            Publish-PublicGateway
        }
        default {
            Invoke-CoreAction $Action
        }
    }
    exit 0
} catch {
    $record = $_
    Write-Host ""
    Write-Host "== StockBoard public gateway error detail ==" -ForegroundColor Red
    Write-Host "ERROR_MESSAGE=$($record.Exception.Message)" -ForegroundColor Red
    Write-Host "ERROR_SCRIPT=$($record.InvocationInfo.ScriptName)" -ForegroundColor Red
    Write-Host "ERROR_LINE=$($record.InvocationInfo.ScriptLineNumber)" -ForegroundColor Red
    Write-Host "ERROR_POSITION=$($record.InvocationInfo.PositionMessage)" -ForegroundColor Red
    Write-Host "ERROR_STACK=$($record.ScriptStackTrace)" -ForegroundColor Red
    exit 1
}
