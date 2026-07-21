@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d C:\aiTrade

set "ACTION=%~1"
if "%ACTION%"=="" goto menu
goto elevate

:menu
echo.
echo StockBoard v2 Private Web (Tailscale Serve)
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
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo StockBoard v2 private web command finished with an error.
)
echo.
pause
exit /b %RC%

# POWERSHELL-BEGIN
$ErrorActionPreference = "Stop"

$Action = [string]$env:STOCKBOARD_WEB_ACTION
$Action = $Action.Trim().ToLowerInvariant()
$ProjectRoot = "C:\aiTrade"
$Launcher = Join-Path $ProjectRoot "stockboard_v2_large.cmd"
$WorkerPort = 8765
$LocalBaseUrl = "http://127.0.0.1:$WorkerPort"
$LocalHealthUrl = "$LocalBaseUrl/api/v2/health"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$UrlFile = Join-Path $RuntimeDir "stockboard_private_web_url.txt"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Resolve-TailscaleExe {
    $command = Get-Command "tailscale.exe" -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        return [string]$command.Source
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
    throw "Install Tailscale for Windows, sign in, then run stockboard_web.cmd again."
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
    Write-Host "Complete the browser sign-in with the Tailscale account used on approved devices."
    [void](Invoke-Tailscale -Exe $Exe -Arguments @("up") -AllowFailure)

    $deadline = (Get-Date).AddMinutes(2)
    do {
        Start-Sleep -Seconds 2
        $status = Get-TailscaleStatusJson -Exe $Exe
        if ($status -and [string]$status.BackendState -eq "Running") {
            return $status
        }
    } while ((Get-Date) -lt $deadline)

    throw "Tailscale login is not complete. Finish the sign-in, then run stockboard_web.cmd again."
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

function Get-PortListeners {
    try {
        return @(
            Get-NetTCPConnection -LocalPort $WorkerPort -State Listen -ErrorAction Stop |
                Select-Object LocalAddress, LocalPort, OwningProcess
        )
    } catch {
        $rows = New-Object System.Collections.Generic.List[object]
        $pattern = "^\s*TCP\s+(\S+):$WorkerPort\s+\S+\s+LISTENING\s+(\d+)\s*$"
        foreach ($line in @(netstat -ano -p tcp)) {
            $match = [regex]::Match([string]$line, $pattern)
            if (-not $match.Success) {
                continue
            }
            $rows.Add([pscustomobject]@{
                LocalAddress = [string]$match.Groups[1].Value
                LocalPort = $WorkerPort
                OwningProcess = [int]$match.Groups[2].Value
            })
        }
        return @($rows)
    }
}

function Test-LoopbackListener {
    $listeners = @(Get-PortListeners)
    if ($listeners.Count -eq 0) {
        return [pscustomobject]@{
            Safe = $false
            Detail = "No StockBoard v2 listener on port $WorkerPort."
            Pids = @()
        }
    }

    $unsafe = @(
        $listeners | Where-Object {
            [string]$_.LocalAddress -notin @("127.0.0.1", "::1")
        }
    )
    if ($unsafe.Count -gt 0) {
        $addresses = @($unsafe | Select-Object -ExpandProperty LocalAddress -Unique) -join ","
        return [pscustomobject]@{
            Safe = $false
            Detail = "Port $WorkerPort is exposed on a non-loopback address: $addresses"
            Pids = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
        }
    }

    return [pscustomobject]@{
        Safe = $true
        Detail = "Port $WorkerPort listens on loopback only."
        Pids = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    }
}

function Get-LocalHealth {
    try {
        $response = Invoke-WebRequest -Uri $LocalHealthUrl -UseBasicParsing -TimeoutSec 3
        return [pscustomobject]@{
            Ok = ($response.StatusCode -eq 200)
            StatusCode = [int]$response.StatusCode
            Error = $null
        }
    } catch {
        return [pscustomobject]@{
            Ok = $false
            StatusCode = $null
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

function Test-ServeTargetsStockBoardV2 {
    param($ServeStatus)

    if (-not $ServeStatus) {
        return $false
    }
    $combined = [string]$ServeStatus.Text
    if ($ServeStatus.Json) {
        $combined += " " + ($ServeStatus.Json | ConvertTo-Json -Depth 20 -Compress)
    }
    return $combined -match "127\.0\.0\.1:8765"
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

    $healthUrl = "$Url/api/v2/health"
    $deadline = (Get-Date).AddSeconds(30)
    $lastError = $null
    do {
        try {
            $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                return [pscustomobject]@{ Ok = $true; Error = $null }
            }
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    return [pscustomobject]@{ Ok = $false; Error = $lastError }
}

function Write-PrivateWebStatus {
    param([string]$Exe)

    Write-Step "StockBoard v2 private web status"
    $local = Get-LocalHealth
    $listener = Test-LoopbackListener
    $tailscaleStatus = Get-TailscaleStatusJson -Exe $Exe
    $serve = Get-ServeStatus -Exe $Exe
    $dnsName = Get-TailscaleDnsName -Status $tailscaleStatus
    $url = if ($dnsName) { "https://$dnsName" } else { $null }
    $serveMatches = Test-ServeTargetsStockBoardV2 -ServeStatus $serve

    Write-Host "LOCAL_HEALTH_OK=$($local.Ok)"
    Write-Host "LOCAL_HEALTH_URL=$LocalHealthUrl"
    Write-Host "LOCAL_WORKER_PORT=$WorkerPort"
    Write-Host "LOCAL_LISTENER_PIDS=$($listener.Pids -join ',')"
    Write-Host "LOOPBACK_ONLY=$($listener.Safe)"
    Write-Host "LOOPBACK_DETAIL=$($listener.Detail)"
    Write-Host "TAILSCALE_STATE=$([string]$tailscaleStatus.BackendState)"
    Write-Host "SERVE_TO_STOCKBOARD_V2=$serveMatches"
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
    Write-Step "Checking StockBoard v2 local server"
    if (-not (Test-Path -LiteralPath $Launcher)) {
        throw "Current launcher was not found: $Launcher"
    }

    $local = Get-LocalHealth
    if (-not $local.Ok) {
        throw "StockBoard v2 is not responding at $LocalHealthUrl. Start it with stockboard_v2_large.cmd first. Detail: $($local.Error)"
    }

    $listener = Test-LoopbackListener
    if (-not $listener.Safe) {
        throw "Unsafe listener configuration. $($listener.Detail)"
    }
    Write-Host $listener.Detail -ForegroundColor Green

    $tailscaleExe = Require-TailscaleExe
    $tailscaleStatus = Ensure-TailscaleConnected -Exe $tailscaleExe

    Write-Step "Enabling Tailscale Serve"
    Write-Host "Target: $LocalBaseUrl"
    [void](Invoke-Tailscale -Exe $tailscaleExe -Arguments @("serve", "--bg", $LocalBaseUrl))

    $serve = Get-ServeStatus -Exe $tailscaleExe
    if (-not (Test-ServeTargetsStockBoardV2 -ServeStatus $serve)) {
        throw "Tailscale Serve did not report the expected StockBoard v2 target $LocalBaseUrl."
    }

    $tailscaleStatus = Get-TailscaleStatusJson -Exe $tailscaleExe
    $dnsName = Get-TailscaleDnsName -Status $tailscaleStatus
    if (-not $dnsName) {
        throw "Tailscale DNS name is unavailable. Check MagicDNS and HTTPS settings in the tailnet."
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
        Write-Host "Remote health verification is not complete: $($remote.Error)" -ForegroundColor Yellow
        Write-Host "The private Serve configuration is active. Run status again after DNS/HTTPS provisioning." -ForegroundColor Yellow
    }
    Write-Host "Access is limited to users and devices allowed by the tailnet. Funnel is not enabled."

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
    $stillMatches = Test-ServeTargetsStockBoardV2 -ServeStatus $serve
    Write-Host "PRIVATE_WEB_ENABLED=$stillMatches"
    if ($stillMatches) {
        throw "The StockBoard v2 Serve target still appears active. Review tailscale serve status."
    }
    Write-Host "StockBoard v2 private HTTPS access is disabled." -ForegroundColor Green
    Write-Host "The local StockBoard v2 processes were not stopped."
}

switch ($Action) {
    "enable" { Enable-PrivateWeb }
    "status" {
        $tailscaleExe = Resolve-TailscaleExe
        if (-not $tailscaleExe) {
            throw "Tailscale is not installed."
        }
        [void](Write-PrivateWebStatus -Exe $tailscaleExe)
    }
    "disable" { Disable-PrivateWeb }
    default {
        Write-Host "Usage: stockboard_web.cmd [enable|status|disable]"
        exit 1
    }
}
