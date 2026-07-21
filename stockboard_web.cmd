@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d C:\aiTrade

set "ACTION=%~1"
if "%ACTION%"=="" goto menu
goto elevate

:menu
echo.
echo StockBoard Private Web (Tailscale Serve)
echo.
echo   1 Enable private HTTPS access
echo   2 Show status
echo   3 Disable private HTTPS access
echo   0 Exit
echo.
set /p "CHOICE=Select: "
if "%CHOICE%"=="1" set "ACTION=enable"
if "%CHOICE%"=="2" set "ACTION=status"
if "%CHOICE%"=="3" set "ACTION=disable"
if "%CHOICE%"=="0" exit /b 0
if "%ACTION%"=="" (
  echo Invalid selection.
  goto menu
)

:elevate
powershell.exe -NoProfile -Command "$p=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if($p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){exit 0}else{exit 1}"
if errorlevel 1 (
  powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%ACTION%' -Verb RunAs"
  exit /b 0
)

goto run

:run
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$raw=Get-Content -LiteralPath '%~f0' -Raw; $marker='# POWERSHELL-BEGIN'; $idx=$raw.LastIndexOf($marker); if($idx -lt 0){throw 'PowerShell marker not found'}; $env:STOCKBOARD_WEB_ACTION='%ACTION%'; Invoke-Expression $raw.Substring($idx + $marker.Length)"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo StockBoard private web command finished with an error.
)
echo.
pause
exit /b %EXIT_CODE%

# POWERSHELL-BEGIN
$ErrorActionPreference = "Stop"

$Action = [string]$env:STOCKBOARD_WEB_ACTION
$Action = $Action.Trim().ToLowerInvariant()
$ProjectRoot = "C:\aiTrade"
$LocalBaseUrl = "http://127.0.0.1:8000"
$LocalHealthUrl = "$LocalBaseUrl/api/health"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime"
$UrlFile = Join-Path $RuntimeDir "stockboard_private_web_url.txt"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Resolve-TailscaleExe {
    $command = Get-Command "tailscale.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"))
    }
    if (${env:ProgramFiles(x86)}) {
        $candidates.Add((Join-Path ${env:ProgramFiles(x86)} "Tailscale\tailscale.exe"))
    }
    $candidates.Add("C:\Program Files\Tailscale\tailscale.exe")
    $candidates.Add("C:\Program Files (x86)\Tailscale\tailscale.exe")

    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Require-TailscaleExe {
    $tailscaleExe = Resolve-TailscaleExe
    if ($tailscaleExe) {
        return $tailscaleExe
    }

    Write-Host "Tailscale is not installed. Opening the official Windows download page." -ForegroundColor Yellow
    Start-Process "https://tailscale.com/download/windows" | Out-Null
    throw "Install Tailscale for Windows, sign in, then run this file again."
}

function Invoke-Tailscale {
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
        Lines = @($output)
    }
}

function Get-TailscaleStatusJson {
    param([string]$Exe)
    $result = Invoke-Tailscale -Exe $Exe -Arguments @("status", "--json") -AllowFailure
    if ($result.ExitCode -ne 0 -or -not $result.Text) {
        return $null
    }
    try {
        return $result.Text | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Ensure-TailscaleConnected {
    param([string]$Exe)

    $status = Get-TailscaleStatusJson -Exe $Exe
    if ($status -and [string]$status.BackendState -eq "Running") {
        return $status
    }

    Write-Step "Connecting Tailscale"
    Write-Host "A browser sign-in may open. Complete the Tailscale login with the account used on your other approved devices."
    [void](Invoke-Tailscale -Exe $Exe -Arguments @("up") -AllowFailure)

    $deadline = (Get-Date).AddMinutes(2)
    do {
        Start-Sleep -Seconds 2
        $status = Get-TailscaleStatusJson -Exe $Exe
        if ($status -and [string]$status.BackendState -eq "Running") {
            return $status
        }
    } while ((Get-Date) -lt $deadline)

    throw "Tailscale login is not complete. Finish the sign-in, then run this file again."
}

function Get-TailscaleDnsName {
    param($Status)
    if (-not $Status -or -not $Status.Self) {
        return $null
    }
    $dnsName = [string]$Status.Self.DNSName
    if (-not $dnsName) {
        return $null
    }
    return $dnsName.Trim().TrimEnd('.')
}

function Test-LoopbackListener {
    try {
        $listeners = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop)
        if ($listeners.Count -eq 0) {
            return [pscustomobject]@{ Safe = $false; Detail = "No listener on port 8000." }
        }
        $unsafe = @($listeners | Where-Object { $_.LocalAddress -notin @("127.0.0.1", "::1") })
        if ($unsafe.Count -gt 0) {
            $addresses = ($unsafe | Select-Object -ExpandProperty LocalAddress -Unique) -join ","
            return [pscustomobject]@{ Safe = $false; Detail = "Port 8000 is exposed on non-loopback address: $addresses" }
        }
        return [pscustomobject]@{ Safe = $true; Detail = "Port 8000 listens on loopback only." }
    } catch {
        $lines = @(netstat -ano -p tcp | Select-String -Pattern "LISTENING")
        $portLines = @($lines | Where-Object { $_.Line -match "127\.0\.0\.1:8000\s" })
        if ($portLines.Count -gt 0) {
            return [pscustomobject]@{ Safe = $true; Detail = "Port 8000 listens on 127.0.0.1." }
        }
        return [pscustomobject]@{ Safe = $false; Detail = "Could not verify a loopback-only listener on port 8000." }
    }
}

function Get-LocalHealth {
    try {
        $response = Invoke-WebRequest -Uri $LocalHealthUrl -UseBasicParsing -TimeoutSec 3
        $body = $response.Content | ConvertFrom-Json
        return [pscustomobject]@{
            Ok = ($response.StatusCode -eq 200 -and [bool]$body.ok)
            Pid = $body.pid
            Error = $null
        }
    } catch {
        return [pscustomobject]@{
            Ok = $false
            Pid = $null
            Error = $_.Exception.Message
        }
    }
}

function Get-ServeStatus {
    param([string]$Exe)
    $jsonResult = Invoke-Tailscale -Exe $Exe -Arguments @("serve", "status", "--json") -AllowFailure
    $json = $null
    if ($jsonResult.ExitCode -eq 0 -and $jsonResult.Text) {
        try {
            $json = $jsonResult.Text | ConvertFrom-Json
        } catch {
            $json = $null
        }
    }
    $textResult = Invoke-Tailscale -Exe $Exe -Arguments @("serve", "status") -AllowFailure
    return [pscustomobject]@{
        Json = $json
        Text = $textResult.Text
        ExitCode = $textResult.ExitCode
    }
}

function Test-ServeTargetsStockBoard {
    param($ServeStatus)
    if (-not $ServeStatus) {
        return $false
    }
    $combined = ""
    if ($ServeStatus.Text) {
        $combined += [string]$ServeStatus.Text
    }
    if ($ServeStatus.Json) {
        $combined += " " + ($ServeStatus.Json | ConvertTo-Json -Depth 20 -Compress)
    }
    return $combined -match "127\.0\.0\.1:8000"
}

function Save-PrivateUrl {
    param([string]$Url)
    if (-not (Test-Path -LiteralPath $RuntimeDir)) {
        New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    }
    Set-Content -LiteralPath $UrlFile -Value $Url -Encoding UTF8
}

function Wait-RemoteHealth {
    param([string]$Url)
    $healthUrl = "$Url/api/health"
    $deadline = (Get-Date).AddSeconds(30)
    $lastError = $null
    do {
        try {
            $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
            $body = $response.Content | ConvertFrom-Json
            if ($response.StatusCode -eq 200 -and [bool]$body.ok) {
                return [pscustomobject]@{ Ok = $true; Error = $null }
            }
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return [pscustomobject]@{ Ok = $false; Error = $lastError }
}

function Write-Status {
    param([string]$Exe)

    Write-Step "StockBoard private web status"
    $local = Get-LocalHealth
    $listener = Test-LoopbackListener
    $tailscaleStatus = Get-TailscaleStatusJson -Exe $Exe
    $serve = Get-ServeStatus -Exe $Exe
    $dnsName = Get-TailscaleDnsName -Status $tailscaleStatus
    $url = if ($dnsName) { "https://$dnsName" } else { $null }
    $serveMatches = Test-ServeTargetsStockBoard -ServeStatus $serve

    Write-Host "LOCAL_HEALTH_OK=$($local.Ok)"
    Write-Host "LOCAL_SERVER_PID=$($local.Pid)"
    Write-Host "LOOPBACK_ONLY=$($listener.Safe)"
    Write-Host "LOOPBACK_DETAIL=$($listener.Detail)"
    Write-Host "TAILSCALE_STATE=$([string]$tailscaleStatus.BackendState)"
    Write-Host "SERVE_TO_STOCKBOARD=$serveMatches"
    Write-Host "PRIVATE_URL=$url"
    if ($serve.Text) {
        Write-Host ""
        Write-Host $serve.Text
    }

    return [pscustomobject]@{
        LocalOk = [bool]$local.Ok
        LoopbackOnly = [bool]$listener.Safe
        TailscaleRunning = ($tailscaleStatus -and [string]$tailscaleStatus.BackendState -eq "Running")
        ServeMatches = [bool]$serveMatches
        Url = $url
    }
}

function Enable-PrivateWeb {
    Write-Step "Checking StockBoard local server"
    $local = Get-LocalHealth
    if (-not $local.Ok) {
        throw "StockBoard is not responding at $LocalHealthUrl. Start it with stockboard_live.cmd first. Detail: $($local.Error)"
    }

    $listener = Test-LoopbackListener
    if (-not $listener.Safe) {
        throw "Unsafe listener configuration. $($listener.Detail) Keep KIWOOM_HOST at 127.0.0.1."
    }
    Write-Host $listener.Detail -ForegroundColor Green

    $tailscaleExe = Require-TailscaleExe
    $tailscaleStatus = Ensure-TailscaleConnected -Exe $tailscaleExe

    Write-Step "Enabling Tailscale Serve"
    Write-Host "Target: $LocalBaseUrl"
    [void](Invoke-Tailscale -Exe $tailscaleExe -Arguments @("serve", "--bg", $LocalBaseUrl))

    $serve = Get-ServeStatus -Exe $tailscaleExe
    if (-not (Test-ServeTargetsStockBoard -ServeStatus $serve)) {
        throw "Tailscale Serve did not report the expected target $LocalBaseUrl."
    }

    $tailscaleStatus = Get-TailscaleStatusJson -Exe $tailscaleExe
    $dnsName = Get-TailscaleDnsName -Status $tailscaleStatus
    if (-not $dnsName) {
        throw "Tailscale DNS name was not available. Check MagicDNS and HTTPS certificate settings in the tailnet."
    }

    $url = "https://$dnsName"
    Save-PrivateUrl -Url $url
    $remote = Wait-RemoteHealth -Url $url

    Write-Host ""
    Write-Host "PRIVATE_WEB_ENABLED=True" -ForegroundColor Green
    Write-Host "PRIVATE_URL=$url" -ForegroundColor Green
    Write-Host "LOCAL_TARGET=$LocalBaseUrl"
    Write-Host "REMOTE_HEALTH_OK=$($remote.Ok)"
    if (-not $remote.Ok) {
        Write-Host "Remote health verification is not complete yet: $($remote.Error)" -ForegroundColor Yellow
        Write-Host "The Serve configuration is active. Re-run status after Tailscale DNS/HTTPS finishes provisioning." -ForegroundColor Yellow
    }
    Write-Host "Access is limited to devices and users allowed by the tailnet. This script does not enable Tailscale Funnel."

    Start-Process $url | Out-Null
}

function Disable-PrivateWeb {
    $tailscaleExe = Resolve-TailscaleExe
    if (-not $tailscaleExe) {
        throw "Tailscale is not installed."
    }

    Write-Step "Disabling Tailscale Serve HTTPS"
    [void](Invoke-Tailscale -Exe $tailscaleExe -Arguments @("serve", "--https=443", "off") -AllowFailure)
    Remove-Item -LiteralPath $UrlFile -Force -ErrorAction SilentlyContinue

    $serve = Get-ServeStatus -Exe $tailscaleExe
    $stillMatches = Test-ServeTargetsStockBoard -ServeStatus $serve
    Write-Host "PRIVATE_WEB_ENABLED=$stillMatches"
    if ($stillMatches) {
        throw "The StockBoard Serve target still appears active. Review 'tailscale serve status'."
    }
    Write-Host "StockBoard private HTTPS access is disabled." -ForegroundColor Green
    Write-Host "The local StockBoard server was not stopped."
}

switch ($Action) {
    "enable" { Enable-PrivateWeb }
    "status" {
        $tailscaleExe = Resolve-TailscaleExe
        if (-not $tailscaleExe) {
            throw "Tailscale is not installed."
        }
        [void](Write-Status -Exe $tailscaleExe)
    }
    "disable" { Disable-PrivateWeb }
    default {
        Write-Host "Usage: stockboard_web.cmd [enable|status|disable]"
        exit 1
    }
}
