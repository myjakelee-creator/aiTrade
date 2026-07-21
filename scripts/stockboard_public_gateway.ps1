param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "status", "publish", "unpublish", "stop")]
    [string]$Action
)

$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$GatewayScript = Join-Path $ProjectRoot "realtime_v2\public_gateway.py"
$GatewayPidFile = Join-Path $RuntimeDir "public_gateway.pid"
$PublicUrlFile = Join-Path $RuntimeDir "stockboard_public_url.txt"
$PrivateHealthUrl = "http://127.0.0.1:8765/api/v2/health"
$PrivateTarget = "http://127.0.0.1:8765"
$GatewayBaseUrl = "http://127.0.0.1:8766"
$GatewayHealthUrl = "$GatewayBaseUrl/api/v2/health"
$GatewaySnapshotUrl = "$GatewayBaseUrl/api/v2/snapshot?limit=1"
$GatewayPort = 8766

Set-Location -LiteralPath $ProjectRoot

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Ensure-RuntimeDir {
    if (-not (Test-Path -LiteralPath $RuntimeDir)) {
        New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    }
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
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:STOCKBOARD_PYTHON64) {
        $candidates.Add([string]$env:STOCKBOARD_PYTHON64)
    }
    try {
        $command = Get-Command python -ErrorAction Stop
        if ($command.Source) { $candidates.Add([string]$command.Source) }
    } catch { }
    foreach ($pattern in @(
        "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
        "C:\Python*\python.exe",
        "C:\Program Files\Python*\python.exe"
    )) {
        Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            ForEach-Object { $candidates.Add($_.FullName) }
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ((Get-PythonBits $candidate) -eq 64) { return $candidate }
    }
    throw "64-bit Python was not found. Set STOCKBOARD_PYTHON64 to a 64-bit python.exe."
}

function Read-Pid([string]$PidFile) {
    if (-not (Test-Path -LiteralPath $PidFile)) { return 0 }
    $raw = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $number = 0
    if ([int]::TryParse([string]$raw, [ref]$number)) { return $number }
    return 0
}

function Test-PidAlive([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-ProcessCommandLine([int]$ProcessId) {
    if ($ProcessId -le 0) { return "" }
    try {
        return [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine
    } catch {
        return ""
    }
}

function Test-PublicGatewayProcess([int]$ProcessId) {
    if (-not (Test-PidAlive $ProcessId)) { return $false }
    $commandLine = Get-ProcessCommandLine $ProcessId
    return $commandLine -match '(?i)realtime_v2[\\/]public_gateway\.py'
}

function Get-PortListeners([int]$Port) {
    try {
        return @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
                Select-Object LocalAddress, LocalPort, OwningProcess
        )
    } catch {
        $rows = New-Object System.Collections.Generic.List[object]
        $pattern = "^\s*TCP\s+(\S+):$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
        foreach ($line in @(netstat -ano -p tcp)) {
            $match = [regex]::Match([string]$line, $pattern)
            if (-not $match.Success) { continue }
            $rows.Add([pscustomobject]@{
                LocalAddress = [string]$match.Groups[1].Value
                LocalPort = $Port
                OwningProcess = [int]$match.Groups[2].Value
            })
        }
        return @($rows)
    }
}

function Get-LoopbackStatus([int]$Port) {
    $listeners = @(Get-PortListeners $Port)
    if ($listeners.Count -eq 0) {
        return [pscustomobject]@{
            Safe = $false
            Detail = "No listener on port $Port."
            Pids = @()
        }
    }
    $unsafe = @(
        $listeners | Where-Object {
            $address = ([string]$_.LocalAddress) -replace '^\[|\]$', ''
            $address -notin @("127.0.0.1", "::1")
        }
    )
    if ($unsafe.Count -gt 0) {
        $addresses = @($unsafe | Select-Object -ExpandProperty LocalAddress -Unique) -join ","
        return [pscustomobject]@{
            Safe = $false
            Detail = "Port $Port is exposed on a non-loopback address: $addresses"
            Pids = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
        }
    }
    return [pscustomobject]@{
        Safe = $true
        Detail = "Port $Port listens on loopback only."
        Pids = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    }
}

function Invoke-Json([string]$Url, [int]$TimeoutSec = 5) {
    return Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec -Headers @{ Accept = "application/json" }
}

function Test-Url([string]$Url, [int]$TimeoutSec = 3) {
    try {
        Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-Url([string]$Url, [int]$TimeoutSec = 15) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Url $Url 3) { return $true }
        Start-Sleep -Milliseconds 300
    }
    return $false
}

function Assert-PrivateWorkerReady {
    if (-not (Test-Url $PrivateHealthUrl 3)) {
        throw "StockBoard v2 private worker is not responding at $PrivateHealthUrl. Start stockboard_v2_large.cmd first."
    }
}

function Get-HttpStatusFromError($ErrorRecord) {
    try { return [int]$ErrorRecord.Exception.Response.StatusCode } catch { }
    try { return [int]$ErrorRecord.Exception.Response.StatusCode.value__ } catch { }
    return 0
}

function Assert-EndpointStatus([string]$Method, [string]$Url, [int]$ExpectedStatus) {
    try {
        if ($Method -eq "GET") {
            Invoke-WebRequest -Uri $Url -Method GET -UseBasicParsing -TimeoutSec 3 | Out-Null
        } else {
            Invoke-WebRequest -Uri $Url -Method $Method -UseBasicParsing -TimeoutSec 3 -Body "{}" -ContentType "application/json" | Out-Null
        }
    } catch {
        $statusCode = Get-HttpStatusFromError $_
        if ($statusCode -eq $ExpectedStatus) { return }
        throw "Endpoint $Method $Url returned HTTP $statusCode instead of $ExpectedStatus. Detail=$($_.Exception.Message)"
    }
    throw "Endpoint $Method $Url was unexpectedly allowed; expected HTTP $ExpectedStatus."
}

function Assert-PublicSnapshotSafe {
    $health = Invoke-Json $GatewayHealthUrl 5
    if (-not $health.ok -or -not $health.read_only -or -not $health.upstream_ok -or [string]$health.service -ne "stockboard_v2_public_gateway") {
        throw "Public gateway health contract failed. ok=$($health.ok) read_only=$($health.read_only) upstream_ok=$($health.upstream_ok) service=$($health.service)"
    }
    $snapshot = Invoke-Json $GatewaySnapshotUrl 5
    if ([string]$snapshot.source -ne "stockboard_v2_public_gateway") {
        throw "Unexpected public snapshot source: $($snapshot.source)"
    }
    $json = $snapshot | ConvertTo-Json -Depth 50 -Compress
    $forbidden = @(
        '"pid"\s*:',
        '"collector_status"\s*:',
        '"daily_state_path"\s*:',
        '"event_log_path"\s*:',
        '"last_error"\s*:',
        '"source_code"\s*:',
        '"registered_code"\s*:',
        '"received_code"\s*:',
        '"raw"\s*:'
    )
    foreach ($pattern in $forbidden) {
        if ($json -match $pattern) {
            throw "Public snapshot contains a forbidden key matching: $pattern"
        }
    }
    Assert-EndpointStatus "GET" "$GatewayBaseUrl/api/v2/display_order?mode=freeze" 403
    Assert-EndpointStatus "POST" "$GatewayBaseUrl/api/v2/snapshot" 405
    return $snapshot
}

function Assert-LocalStartWillRemainPrivate {
    if (Test-Path -LiteralPath $PublicUrlFile) {
        throw "A public Funnel marker exists at $PublicUrlFile. Use publish to resume public access or unpublish to restore private-only access."
    }
    $exe = Resolve-TailscaleExe
    if (-not $exe) { return }
    $status = Get-TailscaleStatusJson $exe
    if (-not $status -or [string]$status.BackendState -ne "Running") { return }
    $funnelText = Get-FunnelStatusText $exe
    if (Test-FunnelTargetsGateway $funnelText) {
        throw "Tailscale Funnel already targets 127.0.0.1:8766. Run unpublish before a local-only start."
    }
}

function Start-PublicGateway {
    Ensure-RuntimeDir
    Assert-LocalStartWillRemainPrivate
    Assert-PrivateWorkerReady
    if (-not (Test-Path -LiteralPath $GatewayScript)) {
        throw "Public gateway script was not found: $GatewayScript"
    }

    if (Test-Url $GatewayHealthUrl 2) {
        $loopback = Get-LoopbackStatus $GatewayPort
        if (-not $loopback.Safe) { throw $loopback.Detail }
        $snapshot = Assert-PublicSnapshotSafe
        Write-Host "PUBLIC_GATEWAY_ALREADY_RUNNING=True" -ForegroundColor Green
        Write-Host "PUBLIC_GATEWAY_ROWS=$($snapshot.row_count)"
        return
    }

    $pidNumber = Read-Pid $GatewayPidFile
    if (Test-PidAlive $pidNumber) {
        if (-not (Test-PublicGatewayProcess $pidNumber)) {
            throw "PID file points to a non-gateway process. PID=$pidNumber"
        }
        Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
    Remove-Item -LiteralPath $GatewayPidFile -Force -ErrorAction SilentlyContinue

    $listeners = @(Get-PortListeners $GatewayPort)
    if ($listeners.Count -gt 0) {
        $details = @($listeners | ForEach-Object { "$($_.LocalAddress):$($_.LocalPort) PID=$($_.OwningProcess)" }) -join "; "
        throw "Port $GatewayPort is already in use: $details"
    }

    $python64 = Resolve-Python64
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $stdout = Join-Path $RuntimeDir "public_gateway_$stamp.out.log"
    $stderr = Join-Path $RuntimeDir "public_gateway_$stamp.err.log"

    Write-Step "Starting StockBoard public read-only gateway"
    Write-Host "PRIVATE_UPSTREAM=$PrivateTarget"
    Write-Host "PUBLIC_GATEWAY_LOCAL=$GatewayBaseUrl"
    Write-Host "PYTHON64=$python64"

    $process = Start-Process `
        -FilePath $python64 `
        -ArgumentList @(
            "realtime_v2\public_gateway.py",
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
    Write-Host "PUBLIC_GATEWAY_STDOUT=$stdout"
    Write-Host "PUBLIC_GATEWAY_STDERR=$stderr"

    try {
        if (-not (Wait-Url $GatewayHealthUrl 20)) {
            Write-Host "---- public gateway stdout tail ----" -ForegroundColor Yellow
            Get-Content -LiteralPath $stdout -Tail 80 -ErrorAction SilentlyContinue
            Write-Host "---- public gateway stderr tail ----" -ForegroundColor Yellow
            Get-Content -LiteralPath $stderr -Tail 80 -ErrorAction SilentlyContinue
            throw "Public gateway did not become healthy."
        }

        $loopback = Get-LoopbackStatus $GatewayPort
        if (-not $loopback.Safe) {
            throw "Unsafe public gateway listener. $($loopback.Detail)"
        }
        $snapshot = Assert-PublicSnapshotSafe
        Write-Host "PUBLIC_GATEWAY_READY=True" -ForegroundColor Green
        Write-Host "PUBLIC_GATEWAY_LOOPBACK_ONLY=True"
        Write-Host "PUBLIC_GATEWAY_ROWS=$($snapshot.row_count)"
        Write-Host "PUBLIC_GATEWAY_READ_ONLY=True"
    } catch {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $GatewayPidFile -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Stop-PublicGateway {
    Ensure-RuntimeDir
    $pidNumber = Read-Pid $GatewayPidFile
    if (Test-PidAlive $pidNumber) {
        if (-not (Test-PublicGatewayProcess $pidNumber)) {
            throw "Refusing to stop non-gateway PID=$pidNumber from $GatewayPidFile"
        }
        Write-Host "Stopping public gateway PID=$pidNumber"
        Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $GatewayPidFile -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500

    $remaining = @(Get-PortListeners $GatewayPort)
    foreach ($listener in $remaining) {
        $listenerPid = [int]$listener.OwningProcess
        if (Test-PublicGatewayProcess $listenerPid) {
            Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 300
    if (@(Get-PortListeners $GatewayPort).Count -gt 0) {
        throw "Port $GatewayPort still has a listener. It was not force-stopped because it is not confirmed as the public gateway."
    }
    Write-Host "PUBLIC_GATEWAY_RUNNING=False" -ForegroundColor Green
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
    return $null
}

function Invoke-Tailscale {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure,
        [switch]$Quiet
    )
    $output = & $Exe @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    if ($text -and -not $Quiet) { Write-Host $text }
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "tailscale $($Arguments -join ' ') failed with exit code ${exitCode}: $text"
    }
    return [pscustomobject]@{ ExitCode = $exitCode; Text = $text }
}

function Get-TailscaleStatusJson([string]$Exe) {
    $result = Invoke-Tailscale -Exe $Exe -Arguments @("status", "--json") -AllowFailure -Quiet
    if ($result.ExitCode -ne 0 -or -not $result.Text) { return $null }
    try { return $result.Text | ConvertFrom-Json } catch { return $null }
}

function Get-TailscaleDnsName($Status) {
    if (-not $Status -or -not $Status.Self) { return $null }
    $name = [string]$Status.Self.DNSName
    if (-not $name) { return $null }
    return $name.Trim().TrimEnd('.')
}

function Require-TailscaleRunning {
    $exe = Resolve-TailscaleExe
    if (-not $exe) { throw "Tailscale is not installed." }
    $status = Get-TailscaleStatusJson $exe
    if (-not $status -or [string]$status.BackendState -ne "Running") {
        throw "Tailscale is not connected."
    }
    return [pscustomobject]@{ Exe = $exe; Status = $status }
}

function Get-FunnelStatusText([string]$Exe) {
    return (Invoke-Tailscale -Exe $Exe -Arguments @("funnel", "status") -AllowFailure -Quiet).Text
}

function Get-ServeStatusText([string]$Exe) {
    return (Invoke-Tailscale -Exe $Exe -Arguments @("serve", "status") -AllowFailure -Quiet).Text
}

function Test-FunnelTargetsGateway([string]$Text) {
    return [string]$Text -match '127\.0\.0\.1:8766'
}

function Test-ServeTargetsPrivate([string]$Text) {
    return [string]$Text -match '127\.0\.0\.1:8765'
}

function Wait-RemotePublicHealth([string]$Url, [int]$TimeoutSec = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $payload = Invoke-RestMethod -Uri "$Url/api/v2/health" -TimeoutSec 5
            if ($payload.ok -and $payload.read_only -and $payload.upstream_ok -and [string]$payload.service -eq "stockboard_v2_public_gateway") {
                return $true
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Disable-PublicFunnel([string]$Exe) {
    $off = Invoke-Tailscale -Exe $Exe -Arguments @("funnel", "--https=443", "off") -AllowFailure -Quiet
    if ($off.ExitCode -ne 0) {
        [void](Invoke-Tailscale -Exe $Exe -Arguments @("funnel", "reset") -AllowFailure -Quiet)
    }
    $deadline = (Get-Date).AddSeconds(10)
    do {
        $text = Get-FunnelStatusText $Exe
        if (-not (Test-FunnelTargetsGateway $text)) { return }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "Tailscale Funnel still targets the public gateway after disable/reset."
}

function Publish-PublicGateway {
    $tailscale = Require-TailscaleRunning
    $confirmation = [string]$env:STOCKBOARD_PUBLIC_CONFIRM
    if ($confirmation -cne "PUBLIC") {
        Write-Host ""
        Write-Host "This changes the ts.net address from tailnet-only Serve to public Funnel." -ForegroundColor Yellow
        Write-Host "Only the sanitized read-only gateway on 127.0.0.1:8766 will be exposed." -ForegroundColor Yellow
        $confirmation = Read-Host "Type PUBLIC to continue"
    }
    if ($confirmation -cne "PUBLIC") {
        Write-Host "PUBLIC_WEB_ENABLED=False"
        Write-Host "Publishing was cancelled before any network setting changed."
        return
    }

    try {
        $existingFunnel = Get-FunnelStatusText $tailscale.Exe
        if (Test-FunnelTargetsGateway $existingFunnel) {
            Write-Step "Temporarily disabling the existing public Funnel for local validation"
            Disable-PublicFunnel $tailscale.Exe
        }
        Remove-Item -LiteralPath $PublicUrlFile -Force -ErrorAction SilentlyContinue
        Start-PublicGateway

        Write-Step "Publishing sanitized gateway with Tailscale Funnel"
        [void](Invoke-Tailscale -Exe $tailscale.Exe -Arguments @("serve", "--https=443", "off") -AllowFailure -Quiet)
        [void](Invoke-Tailscale -Exe $tailscale.Exe -Arguments @("funnel", "--bg", $GatewayBaseUrl))

        $deadline = (Get-Date).AddSeconds(20)
        $funnelText = ""
        do {
            Start-Sleep -Seconds 1
            $funnelText = Get-FunnelStatusText $tailscale.Exe
            if (Test-FunnelTargetsGateway $funnelText) { break }
        } while ((Get-Date) -lt $deadline)
        if (-not (Test-FunnelTargetsGateway $funnelText)) {
            throw "Tailscale Funnel did not report the expected gateway target $GatewayBaseUrl"
        }

        $status = Get-TailscaleStatusJson $tailscale.Exe
        $dnsName = Get-TailscaleDnsName $status
        if (-not $dnsName) { throw "Tailscale DNS name is unavailable." }
        $url = "https://$dnsName"
        if (-not (Wait-RemotePublicHealth $url 30)) {
            throw "Remote public health did not verify the sanitized read-only gateway."
        }
        Set-Content -LiteralPath $PublicUrlFile -Value $url -Encoding UTF8

        Write-Host ""
        Write-Host "PUBLIC_WEB_ENABLED=True" -ForegroundColor Green
        Write-Host "PUBLIC_URL=$url" -ForegroundColor Green
        Write-Host "PUBLIC_TARGET=$GatewayBaseUrl"
        Write-Host "PRIVATE_WORKER_TARGET=$PrivateTarget"
        Write-Host "REMOTE_HEALTH_OK=True"
        Write-Host "PUBLIC_DATA_POLICY=allowlist_read_only"
        Start-Process $url | Out-Null
    } catch {
        $originalError = $_.Exception.Message
        Remove-Item -LiteralPath $PublicUrlFile -Force -ErrorAction SilentlyContinue
        try { Disable-PublicFunnel $tailscale.Exe } catch { Write-Warning $_.Exception.Message }
        try { [void](Restore-PrivateServe $tailscale.Exe) } catch { Write-Warning $_.Exception.Message }
        throw "Public publishing failed. Funnel was disabled and private Serve rollback was attempted. Detail: $originalError"
    }
}

function Restore-PrivateServe([string]$Exe) {
    if (-not (Test-Url $PrivateHealthUrl 3)) {
        Write-Warning "Private StockBoard v2 is not healthy, so private Serve was not restored."
        return $false
    }
    [void](Invoke-Tailscale -Exe $Exe -Arguments @("serve", "--bg", $PrivateTarget))
    $serveText = Get-ServeStatusText $Exe
    if (-not (Test-ServeTargetsPrivate $serveText)) {
        throw "Private Tailscale Serve was not restored to $PrivateTarget"
    }
    return $true
}

function Unpublish-PublicGateway {
    $tailscale = Require-TailscaleRunning
    Write-Step "Disabling public Funnel and restoring private Serve"
    Disable-PublicFunnel $tailscale.Exe
    Remove-Item -LiteralPath $PublicUrlFile -Force -ErrorAction SilentlyContinue
    $restored = Restore-PrivateServe $tailscale.Exe
    Write-Host "PUBLIC_WEB_ENABLED=False" -ForegroundColor Green
    Write-Host "PRIVATE_SERVE_RESTORED=$restored"
    Write-Host "LOCAL_PUBLIC_GATEWAY_RUNNING=$(Test-Url $GatewayHealthUrl 2)"
}

function Stop-AllPublic {
    $exe = Resolve-TailscaleExe
    if ($exe) {
        $status = Get-TailscaleStatusJson $exe
        if ($status -and [string]$status.BackendState -eq "Running") {
            $funnelText = Get-FunnelStatusText $exe
            if (Test-FunnelTargetsGateway $funnelText) {
                Unpublish-PublicGateway
            } else {
                Remove-Item -LiteralPath $PublicUrlFile -Force -ErrorAction SilentlyContinue
            }
        } elseif (Test-Path -LiteralPath $PublicUrlFile) {
            throw "Reconnect Tailscale and unpublish before stopping the gateway; a persistent Funnel may still be configured."
        }
    } elseif (Test-Path -LiteralPath $PublicUrlFile) {
        throw "Tailscale is unavailable. Unpublish the persistent Funnel before stopping the gateway."
    }
    Stop-PublicGateway
}

function Show-PublicStatus {
    Ensure-RuntimeDir
    $privateOk = Test-Url $PrivateHealthUrl 3
    $gatewayOk = Test-Url $GatewayHealthUrl 3
    $pidNumber = Read-Pid $GatewayPidFile
    $pidAlive = Test-PidAlive $pidNumber
    $loopback = Get-LoopbackStatus $GatewayPort
    $snapshotSafe = $false
    $rowCount = 0
    if ($gatewayOk) {
        try {
            $snapshot = Assert-PublicSnapshotSafe
            $snapshotSafe = $true
            $rowCount = [int]$snapshot.row_count
        } catch {
            Write-Warning $_.Exception.Message
        }
    }

    $tailscaleState = "unavailable"
    $funnelText = ""
    $serveText = ""
    $dnsName = ""
    $exe = Resolve-TailscaleExe
    if ($exe) {
        $status = Get-TailscaleStatusJson $exe
        if ($status) {
            $tailscaleState = [string]$status.BackendState
            $dnsName = [string](Get-TailscaleDnsName $status)
        }
        $funnelText = Get-FunnelStatusText $exe
        $serveText = Get-ServeStatusText $exe
    }

    Write-Step "StockBoard public gateway status"
    Write-Host "PRIVATE_WORKER_OK=$privateOk"
    Write-Host "PRIVATE_WORKER_URL=$PrivateHealthUrl"
    Write-Host "PUBLIC_GATEWAY_OK=$gatewayOk"
    Write-Host "PUBLIC_GATEWAY_URL=$GatewayBaseUrl"
    Write-Host "PUBLIC_GATEWAY_PID=$pidNumber"
    Write-Host "PUBLIC_GATEWAY_PID_ALIVE=$pidAlive"
    Write-Host "PUBLIC_GATEWAY_LOOPBACK_ONLY=$($loopback.Safe)"
    Write-Host "PUBLIC_GATEWAY_LISTENER=$($loopback.Detail)"
    Write-Host "PUBLIC_SNAPSHOT_SAFE=$snapshotSafe"
    Write-Host "PUBLIC_ROW_COUNT=$rowCount"
    Write-Host "TAILSCALE_STATE=$tailscaleState"
    Write-Host "FUNNEL_TO_PUBLIC_GATEWAY=$(Test-FunnelTargetsGateway $funnelText)"
    Write-Host "PRIVATE_SERVE_TO_8765=$(Test-ServeTargetsPrivate $serveText)"
    if ($dnsName) { Write-Host "TS_URL=https://$dnsName" }
    if ($funnelText) {
        Write-Host ""
        Write-Host "--- funnel status ---"
        Write-Host $funnelText
    }
    if ($serveText) {
        Write-Host ""
        Write-Host "--- serve status ---"
        Write-Host $serveText
    }
}

switch ($Action) {
    "start" { Start-PublicGateway; break }
    "status" { Show-PublicStatus; break }
    "publish" { Publish-PublicGateway; break }
    "unpublish" { Unpublish-PublicGateway; break }
    "stop" { Stop-AllPublic; break }
}
