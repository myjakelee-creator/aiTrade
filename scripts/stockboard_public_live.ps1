[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "restart", "status", "publish", "unpublish", "stop")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$GatewayScript = Join-Path $ProjectRoot "realtime_v2\public_gateway_live.py"
$GatewayPidFile = Join-Path $RuntimeDir "public_gateway_live.pid"
$GatewayPort = 8767
$LegacyGatewayPort = 8766
$GatewayBaseUrl = "http://127.0.0.1:$GatewayPort"
$GatewayHealthUrl = "$GatewayBaseUrl/api/v2/health"
$GatewaySnapshotUrl = "$GatewayBaseUrl/api/v2/snapshot?limit=1"
$PrivateTarget = "http://127.0.0.1:8765"
$PrivateHealthUrl = "$PrivateTarget/api/v2/health"
$ExpectedVersion = "stockboard_public_live_ui_v1_20260721"
$ExpectedMarker = "STOCKBOARD_PUBLIC_LIVE_UI_V1_20260721"

Set-Location -LiteralPath $ProjectRoot

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Ensure-RuntimeDir {
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
}

function Get-PythonBits([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return 0 }
    try {
        return [int]([string](& $Path -c "import struct; print(struct.calcsize('P') * 8)" 2>$null)).Trim()
    } catch {
        return 0
    }
}

function Resolve-Python64 {
    $candidates = @()
    if ($env:STOCKBOARD_PYTHON64) { $candidates += [string]$env:STOCKBOARD_PYTHON64 }
    try {
        $command = Get-Command python -ErrorAction Stop
        if ($command.Source) { $candidates += [string]$command.Source }
    } catch { }
    $candidates += "C:\Python314\python.exe"
    $candidates += "C:\Users\myjay\AppData\Local\Programs\Python\Python314\python.exe"
    foreach ($pattern in @(
        "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
        "C:\Python*\python.exe",
        "C:\Program Files\Python*\python.exe"
    )) {
        $candidates += @(
            Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending |
                Select-Object -ExpandProperty FullName
        )
    }
    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        if ((Get-PythonBits $candidate) -eq 64) { return $candidate }
    }
    throw "64-bit Python was not found."
}

function Get-ListenerRows([int]$Port) {
    $result = @()
    foreach ($line in @(netstat -ano -p tcp 2>$null)) {
        $match = [regex]::Match(
            [string]$line,
            '^\s*TCP\s+(\S+):(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$'
        )
        if (-not $match.Success) { continue }
        $portNumber = 0
        $processId = 0
        if (-not [int]::TryParse($match.Groups[2].Value, [ref]$portNumber)) { continue }
        if (-not [int]::TryParse($match.Groups[3].Value, [ref]$processId)) { continue }
        if ($portNumber -ne $Port) { continue }
        $result += [pscustomobject]@{
            LocalAddress = [string]$match.Groups[1].Value
            LocalPort = $portNumber
            OwningProcess = $processId
        }
    }
    return $result
}

function Get-ProcessCommandLine([int]$ProcessId) {
    if ($ProcessId -le 0) { return "" }
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
    return $commandLine -match '(?i)realtime_v2[\\/]+public_gateway(?:_live|_core)?\.py'
}

function Stop-ConfirmedGatewayOnPort([int]$Port) {
    foreach ($listener in @(Get-ListenerRows $Port)) {
        $pidNumber = [int]$listener.OwningProcess
        if (-not (Test-IsPublicGatewayProcess $pidNumber)) {
            $commandLine = Get-ProcessCommandLine $pidNumber
            throw "Port $Port is owned by an unconfirmed process. PID=$pidNumber COMMAND=$commandLine"
        }
        Write-Host "Stopping public gateway port=$Port PID=$pidNumber"
        Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
    }
}

function Wait-PortClosed([int]$Port, [int]$TimeoutSec = 10) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        if (@(Get-ListenerRows $Port).Count -eq 0) { return $true }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Test-PrivateWorker {
    try {
        $health = Invoke-RestMethod -Uri $PrivateHealthUrl -TimeoutSec 5
        return [bool]$health.ok
    } catch {
        return $false
    }
}

function Stop-GatewayProcesses {
    Ensure-RuntimeDir
    Stop-ConfirmedGatewayOnPort $GatewayPort
    Stop-ConfirmedGatewayOnPort $LegacyGatewayPort
    if (-not (Wait-PortClosed $GatewayPort 10)) {
        throw "Port $GatewayPort did not close."
    }
    if (-not (Wait-PortClosed $LegacyGatewayPort 10)) {
        throw "Legacy port $LegacyGatewayPort did not close."
    }
    Remove-Item -LiteralPath $GatewayPidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $RuntimeDir "public_gateway.pid") -Force -ErrorAction SilentlyContinue
}

function Wait-GatewayReady([int]$TimeoutSec = 25) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        try {
            $health = Invoke-RestMethod -Uri $GatewayHealthUrl -TimeoutSec 5
            if (
                [bool]$health.ok -and
                [bool]$health.read_only -and
                [bool]$health.upstream_ok -and
                [string]$health.gateway_version -eq $ExpectedVersion -and
                [string]$health.ui_contract -eq $ExpectedMarker
            ) {
                return $health
            }
        } catch { }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Assert-LiveUi {
    $health = Wait-GatewayReady 25
    if (-not $health) {
        throw "Fresh public gateway did not become ready at $GatewayHealthUrl."
    }
    $response = Invoke-WebRequest `
        -Uri "$GatewayBaseUrl/?v=$ExpectedVersion" `
        -UseBasicParsing `
        -TimeoutSec 10 `
        -Headers @{ "Cache-Control" = "no-cache" }
    $html = [string]$response.Content
    foreach ($marker in @(
        $ExpectedMarker,
        "1분대금",
        "5분강도",
        "공개 읽기 전용 · 현재 UI"
    )) {
        if (-not $html.Contains($marker)) {
            throw "Fresh public UI marker is missing: $marker"
        }
    }
    $snapshot = Invoke-RestMethod -Uri $GatewaySnapshotUrl -TimeoutSec 5
    Write-Host "PUBLIC_GATEWAY_VERSION=$($health.gateway_version)" -ForegroundColor Green
    Write-Host "PUBLIC_GATEWAY_PORT=$GatewayPort"
    Write-Host "PUBLIC_UI_SOURCE=$($health.ui_source)"
    Write-Host "PUBLIC_UI_LIVE_SYNC=True" -ForegroundColor Green
    Write-Host "PUBLIC_UI_HAS_1MIN_VALUE=True"
    Write-Host "PUBLIC_UI_HAS_5MIN_STRENGTH=True"
    Write-Host "PUBLIC_GATEWAY_ROWS=$($snapshot.row_count)"
}

function Start-Gateway {
    if (-not (Test-PrivateWorker)) {
        throw "Private StockBoard worker is not healthy at $PrivateHealthUrl."
    }
    if (-not (Test-Path -LiteralPath $GatewayScript)) {
        throw "Fresh gateway script was not found: $GatewayScript"
    }

    Write-Step "Replacing all old public gateways with fresh port 8767"
    Stop-GatewayProcesses

    $python64 = Resolve-Python64
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $stdout = Join-Path $RuntimeDir "public_gateway_live_$stamp.out.log"
    $stderr = Join-Path $RuntimeDir "public_gateway_live_$stamp.err.log"

    $process = Start-Process `
        -FilePath $python64 `
        -ArgumentList @(
            "realtime_v2\public_gateway_live.py",
            "--host", "127.0.0.1",
            "--port", [string]$GatewayPort,
            "--upstream", $PrivateTarget
        ) `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    Set-Content -LiteralPath $GatewayPidFile -Value $process.Id -Encoding ASCII
    Write-Host "PUBLIC_GATEWAY_PID=$($process.Id)"
    Write-Host "PUBLIC_GATEWAY_LOCAL=$GatewayBaseUrl"
    Write-Host "PUBLIC_GATEWAY_STDOUT=$stdout"
    Write-Host "PUBLIC_GATEWAY_STDERR=$stderr"

    try {
        Assert-LiveUi
    } catch {
        Write-Host "---- fresh gateway stdout tail ----" -ForegroundColor Yellow
        Get-Content -LiteralPath $stdout -Tail 80 -ErrorAction SilentlyContinue
        Write-Host "---- fresh gateway stderr tail ----" -ForegroundColor Yellow
        Get-Content -LiteralPath $stderr -Tail 80 -ErrorAction SilentlyContinue
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $GatewayPidFile -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Resolve-TailscaleExe {
    $command = Get-Command "tailscale.exe" -ErrorAction SilentlyContinue
    if ($command -and $command.Source) { return [string]$command.Source }
    foreach ($candidate in @(
        "C:\Program Files\Tailscale\tailscale.exe",
        "C:\Program Files (x86)\Tailscale\tailscale.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw "Tailscale is not installed."
}

function Get-TailscaleDnsName([string]$Exe) {
    try {
        $status = (& $Exe status --json 2>$null | Out-String) | ConvertFrom-Json
        return ([string]$status.Self.DNSName).Trim().TrimEnd(".")
    } catch {
        return ""
    }
}

function Disable-Funnel([string]$Exe) {
    & $Exe funnel --https=443 off 2>$null | Out-Null
    $status = (& $Exe funnel status 2>&1 | Out-String)
    if ($status -match "127\.0\.0\.1:(8766|8767)") {
        & $Exe funnel reset 2>$null | Out-Null
    }
}

function Restore-PrivateServe([string]$Exe) {
    if (Test-PrivateWorker) {
        & $Exe serve --bg $PrivateTarget
        if ($LASTEXITCODE -ne 0) {
            throw "Private Tailscale Serve could not be restored."
        }
    }
}

function Wait-FunnelTarget([string]$Exe, [int]$TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        $text = (& $Exe funnel status 2>&1 | Out-String)
        if ($text -match "127\.0\.0\.1:$GatewayPort") { return $true }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Publish-Gateway {
    Start-Gateway

    $confirmation = [string]$env:STOCKBOARD_PUBLIC_CONFIRM
    if ($confirmation -cne "PUBLIC") {
        Write-Host ""
        Write-Host "Only the fresh read-only gateway on 127.0.0.1:$GatewayPort will be public." -ForegroundColor Yellow
        $confirmation = Read-Host "Type PUBLIC to continue"
    }
    if ($confirmation -cne "PUBLIC") {
        Write-Host "PUBLIC_WEB_ENABLED=False"
        return
    }

    $exe = Resolve-TailscaleExe
    & $exe serve --https=443 off 2>$null | Out-Null

    Write-Step "Publishing fresh current UI with Tailscale Funnel"
    & $exe funnel --bg ([string]$GatewayPort)
    if ($LASTEXITCODE -ne 0) {
        throw "Tailscale Funnel failed with exit code $LASTEXITCODE."
    }
    if (-not (Wait-FunnelTarget $exe 60)) {
        throw "Tailscale Funnel did not target 127.0.0.1:$GatewayPort."
    }

    $dnsName = Get-TailscaleDnsName $exe
    if (-not $dnsName) { throw "Tailscale DNS name is unavailable." }
    $url = "https://$dnsName"

    Write-Host "PUBLIC_WEB_ENABLED=True" -ForegroundColor Green
    Write-Host "PUBLIC_URL=$url" -ForegroundColor Green
    Write-Host "PUBLIC_TARGET=http://127.0.0.1:$GatewayPort"
    Write-Host "PUBLIC_GATEWAY_VERSION=$ExpectedVersion"
    Write-Host "PUBLIC_UI_LIVE_SYNC=True"
    Start-Process "$url/?v=$ExpectedVersion" | Out-Null
}

function Unpublish-Gateway {
    $exe = Resolve-TailscaleExe
    Write-Step "Disabling Funnel and restoring private Serve"
    Disable-Funnel $exe
    Restore-PrivateServe $exe
    Write-Host "PUBLIC_WEB_ENABLED=False" -ForegroundColor Green
    Write-Host "PRIVATE_SERVE_RESTORED=True"
}

function Show-Status {
    Write-Step "StockBoard fresh public gateway status"
    Write-Host "PRIVATE_WORKER_OK=$(Test-PrivateWorker)"
    Write-Host "PUBLIC_GATEWAY_PORT=$GatewayPort"
    try {
        $health = Invoke-RestMethod -Uri $GatewayHealthUrl -TimeoutSec 5
        $health | Format-List *
    } catch {
        Write-Host "PUBLIC_GATEWAY_OK=False"
    }
    $exe = Resolve-TailscaleExe
    Write-Host ""
    & $exe funnel status
    Write-Host ""
    & $exe serve status
}

try {
    switch ($Action) {
        "start" { Start-Gateway }
        "restart" { Start-Gateway }
        "status" { Show-Status }
        "publish" { Publish-Gateway }
        "unpublish" { Unpublish-Gateway }
        "stop" {
            $exe = Resolve-TailscaleExe
            Disable-Funnel $exe
            Restore-PrivateServe $exe
            Stop-GatewayProcesses
            Write-Host "PUBLIC_GATEWAY_RUNNING=False" -ForegroundColor Green
        }
    }
    exit 0
} catch {
    $record = $_
    Write-Host ""
    Write-Host "== StockBoard fresh public gateway error ==" -ForegroundColor Red
    Write-Host "ERROR_MESSAGE=$($record.Exception.Message)" -ForegroundColor Red
    Write-Host "ERROR_SCRIPT=$($record.InvocationInfo.ScriptName)" -ForegroundColor Red
    Write-Host "ERROR_LINE=$($record.InvocationInfo.ScriptLineNumber)" -ForegroundColor Red
    Write-Host "ERROR_POSITION=$($record.InvocationInfo.PositionMessage)" -ForegroundColor Red
    Write-Host "ERROR_STACK=$($record.ScriptStackTrace)" -ForegroundColor Red
    exit 1
}
