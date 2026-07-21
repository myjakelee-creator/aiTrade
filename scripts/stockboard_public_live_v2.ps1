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
$LastErrorFile = Join-Path $RuntimeDir "public_gateway_live_last_error.txt"
$GatewayPort = 8767
$GatewayBaseUrl = "http://127.0.0.1:$GatewayPort"
$GatewayHealthUrl = "$GatewayBaseUrl/api/v2/health"
$GatewaySnapshotUrl = "$GatewayBaseUrl/api/v2/snapshot?limit=1"
$PrivateBaseUrl = "http://127.0.0.1:8765"
$PrivateHealthUrl = "$PrivateBaseUrl/api/v2/health"
$ExpectedVersion = "stockboard_public_live_ui_v1_20260721"
$ExpectedContract = "STOCKBOARD_PUBLIC_LIVE_UI_V1_20260721"
$LauncherVersion = "stockboard_public_live_launcher_ascii_v3_20260722"

Set-Location -LiteralPath $ProjectRoot
New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
Remove-Item -LiteralPath $LastErrorFile -Force -ErrorAction SilentlyContinue

Write-Host "PUBLIC_SCRIPT_VERSION=$LauncherVersion" -ForegroundColor Cyan
Write-Host "PUBLIC_ACTION=$Action"
Write-Host "PUBLIC_PRIVATE_TARGET=$PrivateBaseUrl"
Write-Host "PUBLIC_GATEWAY_TARGET=$GatewayBaseUrl"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
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
    $candidates = @()
    if ($env:STOCKBOARD_PYTHON64) { $candidates += [string]$env:STOCKBOARD_PYTHON64 }
    $candidates += "C:\Python314\python.exe"
    $candidates += "C:\Users\myjay\AppData\Local\Programs\Python\Python314\python.exe"
    try {
        $command = Get-Command python -ErrorAction Stop
        if ($command.Source) { $candidates += [string]$command.Source }
    } catch { }
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
    foreach ($candidate in @($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if ((Get-PythonBits $candidate) -eq 64) { return [string]$candidate }
    }
    throw "64-bit Python was not found. Set STOCKBOARD_PYTHON64 to python.exe."
}

function Get-ListenerPids([int]$Port) {
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
        if ($portNumber -eq $Port -and $result -notcontains $processId) {
            $result += $processId
        }
    }
    return @($result)
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

function Read-GatewayPid {
    if (-not (Test-Path -LiteralPath $GatewayPidFile)) { return 0 }
    $raw = Get-Content -LiteralPath $GatewayPidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $number = 0
    if ([int]::TryParse([string]$raw, [ref]$number)) { return $number }
    return 0
}

function Test-PidAlive([int]$ProcessId) {
    return $ProcessId -gt 0 -and $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Stop-LiveGateway {
    Write-Step "Stopping public gateway on port 8767"
    $recordedPid = Read-GatewayPid
    $listenerPids = @(Get-ListenerPids $GatewayPort)
    $allPids = @($listenerPids + @($recordedPid) | Where-Object { $_ -gt 0 } | Select-Object -Unique)

    foreach ($pidNumber in $allPids) {
        if (-not (Test-PidAlive $pidNumber)) { continue }
        $commandLine = Get-ProcessCommandLine $pidNumber
        $confirmed = $commandLine -match '(?i)realtime_v2[\\/]public_gateway_live\.py'
        if (-not $confirmed -and $listenerPids -contains $pidNumber) {
            throw "Port $GatewayPort is owned by an unconfirmed process. PID=$pidNumber COMMAND=$commandLine"
        }
        if ($confirmed -or $pidNumber -eq $recordedPid) {
            Write-Host "Stopping gateway PID=$pidNumber"
            Stop-Process -Id $pidNumber -Force -ErrorAction Stop
        }
    }

    Remove-Item -LiteralPath $GatewayPidFile -Force -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddSeconds(8)
    do {
        if (@(Get-ListenerPids $GatewayPort).Count -eq 0) {
            Write-Host "PUBLIC_GATEWAY_RUNNING=False"
            return
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw "Port $GatewayPort did not close."
}

function Get-Json([string]$Url, [int]$TimeoutSec = 5) {
    return Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec -Headers @{ Accept = "application/json" }
}

function Get-Html([string]$Url) {
    $response = Invoke-WebRequest `
        -Uri $Url `
        -UseBasicParsing `
        -TimeoutSec 10 `
        -Headers @{ "Cache-Control" = "no-cache"; Pragma = "no-cache" }
    return [string]$response.Content
}

function Test-PrivateWorker {
    try {
        $health = Get-Json $PrivateHealthUrl 5
        return [bool]$health.ok
    } catch {
        return $false
    }
}

function Assert-PrivateCurrentUi {
    if (-not (Test-PrivateWorker)) {
        throw "Private StockBoard is not healthy at $PrivateHealthUrl."
    }
    $html = Get-Html "$PrivateBaseUrl/?public_preflight=$LauncherVersion"
    foreach ($marker in @(
        "StockBoard v2",
        "/api/v2/stream",
        "trade_value_1m_eok",
        "strength_5m"
    )) {
        if (-not $html.Contains($marker)) {
            throw "Private current UI marker is missing: $marker"
        }
    }
    Write-Host "PRIVATE_UI_CURRENT=True" -ForegroundColor Green
    Write-Host "PRIVATE_UI_HAS_1MIN_VALUE=True"
    Write-Host "PRIVATE_UI_HAS_5MIN_STRENGTH=True"
}

function Wait-GatewayReady([int]$ProcessId, [string]$Stdout, [string]$Stderr) {
    $deadline = (Get-Date).AddSeconds(25)
    do {
        if (-not (Test-PidAlive $ProcessId)) {
            Write-Host "---- gateway stdout ----" -ForegroundColor Yellow
            Get-Content -LiteralPath $Stdout -Tail 100 -ErrorAction SilentlyContinue
            Write-Host "---- gateway stderr ----" -ForegroundColor Yellow
            Get-Content -LiteralPath $Stderr -Tail 100 -ErrorAction SilentlyContinue
            throw "Gateway process exited before port $GatewayPort became ready."
        }
        try {
            $health = Get-Json $GatewayHealthUrl 5
            if (
                [bool]$health.ok -and
                [bool]$health.read_only -and
                [bool]$health.upstream_ok -and
                [string]$health.gateway_version -eq $ExpectedVersion -and
                [string]$health.ui_contract -eq $ExpectedContract
            ) {
                return $health
            }
        } catch { }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)

    Write-Host "---- gateway stdout ----" -ForegroundColor Yellow
    Get-Content -LiteralPath $Stdout -Tail 100 -ErrorAction SilentlyContinue
    Write-Host "---- gateway stderr ----" -ForegroundColor Yellow
    Get-Content -LiteralPath $Stderr -Tail 100 -ErrorAction SilentlyContinue
    throw "Gateway did not become healthy at $GatewayHealthUrl."
}

function Assert-PublicCurrentUi {
    $html = Get-Html "$GatewayBaseUrl/?v=$ExpectedVersion"
    foreach ($marker in @(
        $ExpectedContract,
        "trade_value_1m_eok",
        "strength_5m",
        "stockboard-public-live-ui-script"
    )) {
        if (-not $html.Contains($marker)) {
            throw "Public current UI marker is missing: $marker"
        }
    }
    $snapshot = Get-Json $GatewaySnapshotUrl 5
    Write-Host "PUBLIC_GATEWAY_VERSION=$ExpectedVersion" -ForegroundColor Green
    Write-Host "PUBLIC_GATEWAY_PORT=$GatewayPort"
    Write-Host "PUBLIC_UI_SOURCE=live_private_worker_html_per_request"
    Write-Host "PUBLIC_UI_LIVE_SYNC=True" -ForegroundColor Green
    Write-Host "PUBLIC_UI_HAS_1MIN_VALUE=True"
    Write-Host "PUBLIC_UI_HAS_5MIN_STRENGTH=True"
    Write-Host "PUBLIC_GATEWAY_ROWS=$($snapshot.row_count)"
}

function Start-LiveGateway {
    Write-Step "Preflight"
    Assert-PrivateCurrentUi
    if (-not (Test-Path -LiteralPath $GatewayScript)) {
        throw "Gateway script was not found: $GatewayScript"
    }
    $python64 = Resolve-Python64
    Write-Host "PYTHON64=$python64"
    Write-Host "PYTHON64_BITS=$(Get-PythonBits $python64)"

    & $python64 -m py_compile $GatewayScript
    if ($LASTEXITCODE -ne 0) {
        throw "Python syntax check failed for $GatewayScript."
    }
    Write-Host "PUBLIC_GATEWAY_PY_COMPILE=True"

    Stop-LiveGateway

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $stdout = Join-Path $RuntimeDir "public_gateway_live_$stamp.out.log"
    $stderr = Join-Path $RuntimeDir "public_gateway_live_$stamp.err.log"
    Write-Step "Starting public current UI gateway"
    Write-Host "PUBLIC_GATEWAY_STDOUT=$stdout"
    Write-Host "PUBLIC_GATEWAY_STDERR=$stderr"

    $process = Start-Process `
        -FilePath $python64 `
        -ArgumentList @(
            "realtime_v2\public_gateway_live.py",
            "--host", "127.0.0.1",
            "--port", [string]$GatewayPort,
            "--upstream", $PrivateBaseUrl
        ) `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    Set-Content -LiteralPath $GatewayPidFile -Value $process.Id -Encoding ASCII
    Write-Host "PUBLIC_GATEWAY_PID=$($process.Id)"
    $health = Wait-GatewayReady $process.Id $stdout $stderr
    Write-Host "PUBLIC_GATEWAY_HEALTH=True" -ForegroundColor Green
    Write-Host "PUBLIC_GATEWAY_HEALTH_VERSION=$($health.gateway_version)"
    Assert-PublicCurrentUi
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

function Get-TailscaleDnsName([string]$Exe) {
    $json = (& $Exe status --json 2>$null | Out-String)
    if (-not $json) { return "" }
    try {
        $status = $json | ConvertFrom-Json
        return ([string]$status.Self.DNSName).Trim().TrimEnd(".")
    } catch {
        return ""
    }
}

function Disable-Funnel([string]$Exe) {
    & $Exe funnel --https=443 off
    if ($LASTEXITCODE -ne 0) {
        & $Exe funnel reset
    }
}

function Restore-PrivateServe([string]$Exe) {
    if (-not (Test-PrivateWorker)) {
        throw "Private StockBoard is not healthy; private Serve was not restored."
    }
    & $Exe serve --bg $PrivateBaseUrl
    if ($LASTEXITCODE -ne 0) {
        throw "Private Tailscale Serve could not be restored."
    }
}

function Publish-LiveGateway {
    Start-LiveGateway
    $confirmation = [string]$env:STOCKBOARD_PUBLIC_CONFIRM
    if ($confirmation -cne "PUBLIC") {
        Write-Host ""
        Write-Host "Only the read-only gateway on 127.0.0.1:$GatewayPort will be public." -ForegroundColor Yellow
        $confirmation = Read-Host "Type PUBLIC to continue"
    }
    if ($confirmation -cne "PUBLIC") {
        Write-Host "PUBLIC_WEB_ENABLED=False"
        return
    }

    $exe = Resolve-TailscaleExe
    & $exe serve --https=443 off
    Write-Step "Publishing port 8767 with Tailscale Funnel"
    & $exe funnel --bg ([string]$GatewayPort)
    if ($LASTEXITCODE -ne 0) {
        throw "Tailscale Funnel failed with exit code $LASTEXITCODE."
    }

    $statusText = (& $exe funnel status 2>&1 | Out-String)
    if ($statusText -notmatch "127\.0\.0\.1:$GatewayPort") {
        throw "Tailscale Funnel does not target 127.0.0.1:$GatewayPort. Status=$statusText"
    }
    $dnsName = Get-TailscaleDnsName $exe
    if (-not $dnsName) { throw "Tailscale DNS name is unavailable." }
    $url = "https://$dnsName"
    Write-Host "PUBLIC_WEB_ENABLED=True" -ForegroundColor Green
    Write-Host "PUBLIC_URL=$url" -ForegroundColor Green
    Write-Host "PUBLIC_TARGET=$GatewayBaseUrl"
    Write-Host "PUBLIC_GATEWAY_VERSION=$ExpectedVersion"
    Start-Process $url | Out-Null
}

function Unpublish-LiveGateway {
    $exe = Resolve-TailscaleExe
    Write-Step "Disabling Funnel and restoring private Serve"
    Disable-Funnel $exe
    Restore-PrivateServe $exe
    Write-Host "PUBLIC_WEB_ENABLED=False" -ForegroundColor Green
    Write-Host "PRIVATE_SERVE_RESTORED=True"
}

function Show-Status {
    Write-Step "Public live gateway status"
    Write-Host "PRIVATE_WORKER_OK=$(Test-PrivateWorker)"
    Write-Host "PUBLIC_GATEWAY_PORT=$GatewayPort"
    Write-Host "PUBLIC_GATEWAY_PID=$(Read-GatewayPid)"
    try {
        $health = Get-Json $GatewayHealthUrl 5
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
        "start" { Start-LiveGateway }
        "restart" { Start-LiveGateway }
        "status" { Show-Status }
        "publish" { Publish-LiveGateway }
        "unpublish" { Unpublish-LiveGateway }
        "stop" {
            Stop-LiveGateway
            Write-Host "PUBLIC_GATEWAY_RUNNING=False" -ForegroundColor Green
        }
    }
    exit 0
} catch {
    $record = $_
    $details = @(
        "TIME=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        "PUBLIC_SCRIPT_VERSION=$LauncherVersion",
        "ACTION=$Action",
        "ERROR_MESSAGE=$($record.Exception.Message)",
        "ERROR_SCRIPT=$($record.InvocationInfo.ScriptName)",
        "ERROR_LINE=$($record.InvocationInfo.ScriptLineNumber)",
        "ERROR_POSITION=$($record.InvocationInfo.PositionMessage)",
        "ERROR_STACK=$($record.ScriptStackTrace)"
    )
    $details | Set-Content -LiteralPath $LastErrorFile -Encoding UTF8
    Write-Host ""
    Write-Host "== StockBoard public live gateway error ==" -ForegroundColor Red
    $details | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    Write-Host "PUBLIC_ERROR_FILE=$LastErrorFile" -ForegroundColor Yellow
    exit 1
}
