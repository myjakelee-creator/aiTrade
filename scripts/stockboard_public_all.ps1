[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "status", "stop")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\aiTrade"
$PrivateLauncher = Join-Path $ProjectRoot "stockboard_v2_large.cmd"
$PublicLauncher = Join-Path $ProjectRoot "scripts\stockboard_public_live_v2.ps1"
$PrivateHealthUrl = "http://127.0.0.1:8765/api/v2/health"
$PublicHealthUrl = "http://127.0.0.1:8767/api/v2/health"
$ExpectedCleanup = "stockboard_public_chrome_cleanup_v3_20260722"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$LastErrorFile = Join-Path $RuntimeDir "stockboard_public_all_last_error.txt"
$LauncherVersion = "stockboard_public_all_v2_20260722"

Set-Location -LiteralPath $ProjectRoot
New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
Remove-Item -LiteralPath $LastErrorFile -Force -ErrorAction SilentlyContinue

Write-Host "ALL_LAUNCHER_VERSION=$LauncherVersion" -ForegroundColor Cyan
Write-Host "ALL_ACTION=$Action"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Resolve-TailscaleExe {
    $command = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source) { return [string]$command.Source }
    foreach ($candidate in @(
        "C:\Program Files\Tailscale\tailscale.exe",
        "C:\Program Files (x86)\Tailscale\tailscale.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw "Tailscale is not installed."
}

function Get-Json([string]$Url, [int]$TimeoutSec = 5) {
    return Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec -Headers @{
        Accept = "application/json"
        "Cache-Control" = "no-cache"
        Pragma = "no-cache"
    }
}

function Get-Health([string]$Url) {
    try {
        return Get-Json $Url 5
    } catch {
        return $null
    }
}

function Wait-Health(
    [string]$Url,
    [int]$TimeoutSec,
    [scriptblock]$Accept
) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $last = $null
    do {
        $last = Get-Health $Url
        if ($last -and (& $Accept $last)) { return $last }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "Health check did not become ready: $Url"
}

function Invoke-Cmd([string]$Path, [string[]]$Arguments) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Launcher was not found: $Path"
    }
    & $Path @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "Launcher failed with exit code ${code}: $Path $($Arguments -join ' ')"
    }
}

function Get-TailscaleStatus([string]$Exe) {
    try {
        $raw = (& $Exe status --json 2>$null | Out-String)
        if (-not $raw) { return $null }
        return $raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Ensure-TailscaleRunning([string]$Exe) {
    Write-Step "Checking Tailscale"
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $status = Get-TailscaleStatus $Exe
        if ($status -and [string]$status.BackendState -eq "Running") {
            Write-Host "TAILSCALE_RUNNING=True" -ForegroundColor Green
            return $status
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    Write-Host "Tailscale is not connected. Running tailscale up." -ForegroundColor Yellow
    & $Exe up

    $deadline = (Get-Date).AddMinutes(2)
    do {
        $status = Get-TailscaleStatus $Exe
        if ($status -and [string]$status.BackendState -eq "Running") {
            Write-Host "TAILSCALE_RUNNING=True" -ForegroundColor Green
            return $status
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    throw "Tailscale did not reach Running state. Complete sign-in and run the launcher again."
}

function Get-PublicUrl([string]$Exe) {
    $status = Get-TailscaleStatus $Exe
    if (-not $status -or -not $status.Self) { return "" }
    $dns = ([string]$status.Self.DNSName).Trim().TrimEnd(".")
    if (-not $dns) { return "" }
    return "https://$dns"
}

function Start-PrivateWorker {
    Write-Step "Checking private StockBoard on port 8765"
    $health = Get-Health $PrivateHealthUrl
    if ($health -and [bool]$health.ok) {
        Write-Host "PRIVATE_WORKER_ALREADY_RUNNING=True" -ForegroundColor Green
        return $health
    }

    Write-Host "Starting StockBoard v2. Complete Kiwoom login if the login window appears." -ForegroundColor Yellow
    Invoke-Cmd $PrivateLauncher @("start-fast")
    $health = Wait-Health $PrivateHealthUrl 240 {
        param($value)
        return [bool]$value.ok
    }
    Write-Host "PRIVATE_WORKER_READY=True" -ForegroundColor Green
    return $health
}

function Publish-PublicGateway {
    Write-Step "Starting public gateway and Funnel"
    if (-not (Test-Path -LiteralPath $PublicLauncher)) {
        throw "Public launcher was not found: $PublicLauncher"
    }

    $previous = [Environment]::GetEnvironmentVariable("STOCKBOARD_PUBLIC_CONFIRM", "Process")
    try {
        $env:STOCKBOARD_PUBLIC_CONFIRM = "PUBLIC"
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PublicLauncher -Action publish
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            throw "Public publish launcher failed with exit code $code."
        }
    } finally {
        if ($null -eq $previous) {
            Remove-Item Env:STOCKBOARD_PUBLIC_CONFIRM -ErrorAction SilentlyContinue
        } else {
            $env:STOCKBOARD_PUBLIC_CONFIRM = $previous
        }
    }

    $health = Wait-Health $PublicHealthUrl 45 {
        param($value)
        return (
            [bool]$value.ok -and
            [bool]$value.read_only -and
            [bool]$value.upstream_ok -and
            [string]$value.public_chrome_cleanup -eq $ExpectedCleanup
        )
    }
    Write-Host "PUBLIC_GATEWAY_READY=True" -ForegroundColor Green
    Write-Host "PUBLIC_CHROME_CLEANUP=$($health.public_chrome_cleanup)"
    return $health
}

function Start-All {
    $tailscale = Resolve-TailscaleExe
    [void](Ensure-TailscaleRunning $tailscale)
    [void](Start-PrivateWorker)
    [void](Publish-PublicGateway)

    $funnelStatus = (& $tailscale funnel status 2>&1 | Out-String)
    if ($funnelStatus -notmatch "127\.0\.0\.1:8767") {
        throw "Tailscale Funnel does not target 127.0.0.1:8767. Status=$funnelStatus"
    }

    $url = Get-PublicUrl $tailscale
    if (-not $url) { throw "Tailscale public DNS name is unavailable." }

    Write-Host ""
    Write-Host "ALL_PUBLIC_READY=True" -ForegroundColor Green
    Write-Host "PRIVATE_TARGET=http://127.0.0.1:8765"
    Write-Host "PUBLIC_TARGET=http://127.0.0.1:8767"
    Write-Host "PUBLIC_URL=$url" -ForegroundColor Green
    Write-Host ""
    Write-Host "The public StockBoard is ready."
}

function Show-AllStatus {
    Write-Step "StockBoard public all-in-one status"
    $private = Get-Health $PrivateHealthUrl
    $public = Get-Health $PublicHealthUrl
    Write-Host "PRIVATE_WORKER_OK=$([bool]($private -and $private.ok))"
    Write-Host "PUBLIC_GATEWAY_OK=$([bool]($public -and $public.ok))"
    if ($public) {
        Write-Host "PUBLIC_READ_ONLY=$([bool]$public.read_only)"
        Write-Host "PUBLIC_UPSTREAM_OK=$([bool]$public.upstream_ok)"
        Write-Host "PUBLIC_CHROME_CLEANUP=$($public.public_chrome_cleanup)"
    }

    try {
        $tailscale = Resolve-TailscaleExe
        $status = Get-TailscaleStatus $tailscale
        Write-Host "TAILSCALE_STATE=$([string]$status.BackendState)"
        Write-Host "PUBLIC_URL=$(Get-PublicUrl $tailscale)"
        Write-Host ""
        & $tailscale funnel status
    } catch {
        Write-Host "TAILSCALE_STATUS_ERROR=$($_.Exception.Message)"
    }
}

function Stop-All {
    Write-Step "Stopping public access and StockBoard"
    try {
        $tailscale = Resolve-TailscaleExe
        & $tailscale funnel --https=443 off
        if ($LASTEXITCODE -ne 0) { & $tailscale funnel reset }
        & $tailscale serve --https=443 off
    } catch {
        Write-Host "TAILSCALE_STOP_WARNING=$($_.Exception.Message)" -ForegroundColor Yellow
    }

    if (Test-Path -LiteralPath $PublicLauncher) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PublicLauncher -Action stop
        if ($LASTEXITCODE -ne 0) {
            Write-Host "PUBLIC_GATEWAY_STOP_WARNING=True" -ForegroundColor Yellow
        }
    }

    if (Test-Path -LiteralPath $PrivateLauncher) {
        & $PrivateLauncher stop
        if ($LASTEXITCODE -ne 0) {
            throw "Private StockBoard stop failed with exit code $LASTEXITCODE."
        }
    }

    Write-Host "ALL_PUBLIC_READY=False" -ForegroundColor Green
    Write-Host "ALL_STOCKBOARD_STOPPED=True" -ForegroundColor Green
}

try {
    switch ($Action) {
        "start" { Start-All }
        "status" { Show-AllStatus }
        "stop" { Stop-All }
    }
    exit 0
} catch {
    $record = $_
    $details = @(
        "TIME=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        "ALL_LAUNCHER_VERSION=$LauncherVersion",
        "ACTION=$Action",
        "ERROR_MESSAGE=$($record.Exception.Message)",
        "ERROR_SCRIPT=$($record.InvocationInfo.ScriptName)",
        "ERROR_LINE=$($record.InvocationInfo.ScriptLineNumber)",
        "ERROR_POSITION=$($record.InvocationInfo.PositionMessage)",
        "ERROR_STACK=$($record.ScriptStackTrace)"
    )
    $details | Set-Content -LiteralPath $LastErrorFile -Encoding UTF8
    Write-Host ""
    Write-Host "== StockBoard public all-in-one launcher error ==" -ForegroundColor Red
    $details | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    Write-Host "ALL_ERROR_FILE=$LastErrorFile" -ForegroundColor Yellow
    exit 1
}
