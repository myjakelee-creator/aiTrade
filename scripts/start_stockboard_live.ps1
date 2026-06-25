$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\aiTrade"
$Python32 = "C:\Users\myjay\AppData\Local\Programs\Python\Python310-32\python.exe"
$BoardUrl = "http://127.0.0.1:8000/stockboard_v0_3_0_sample.html"
$Top100Url = "http://127.0.0.1:8000/api/top100"
$ProviderStatusUrl = "http://127.0.0.1:8000/api/realtime_provider_status"
$RealtimeStatusUrl = "http://127.0.0.1:8000/api/realtime_status"
$RealtimePatchUrl = "http://127.0.0.1:8000/api/realtime_patch"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Fail {
    param([string]$Message)
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host "Server startup stopped. Check the visible server PowerShell window if it was opened." -ForegroundColor Yellow
    exit 1
}

function Get-ListenPid8000 {
    $connections = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if (-not $connections) {
        return $null
    }
    return @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
}

function Get-RowsFromResponse {
    param($Response)
    if ($Response -is [array]) {
        return $Response
    }

    $propertyNames = @($Response.PSObject.Properties | Select-Object -ExpandProperty Name)
    if ($propertyNames -contains "rows") {
        return @($Response.rows)
    }
    if ($propertyNames -contains "data") {
        return @($Response.data)
    }
    return @($Response)
}

function Require-True {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        Fail $Message
    }
}

Write-Step "StockBoard live launcher"
Set-Location -LiteralPath $ProjectRoot
Write-Host "Project root: $ProjectRoot"

Write-Step "Checking 32-bit Python"
if (-not (Test-Path -LiteralPath $Python32)) {
    Fail "32-bit Python was not found: $Python32"
}

$pythonInfo = & $Python32 -c "import sys, platform; print(sys.executable); print(platform.architecture()[0]); print(sys.version)"
$pythonPath = $pythonInfo[0]
$pythonBitness = $pythonInfo[1]
Write-Host "Python path: $pythonPath"
Write-Host "Python bitness: $pythonBitness"
Require-True ($pythonBitness -eq "32bit") "Configured Python is not 32-bit."

Write-Step "Stopping existing 8000 listener only"
$existingPids = Get-ListenPid8000
if ($existingPids) {
    foreach ($pidToStop in $existingPids) {
        Write-Host "Stopping PID listening on port 8000: $pidToStop" -ForegroundColor Yellow
        Stop-Process -Id $pidToStop -Force
    }
    Start-Sleep -Seconds 2
} else {
    Write-Host "No existing 8000 listener."
}

$remainingPids = Get-ListenPid8000
if ($remainingPids) {
    Fail "Port 8000 is still listening after stop attempt. PIDs: $($remainingPids -join ', ')"
}

Write-Step "Starting StockBoard server with realtime flags"
$serverCommand = @"
Set-Location -LiteralPath '$ProjectRoot'
`$env:STOCKBOARD_ENABLE_COM_REALTIME='1'
`$env:STOCKBOARD_REGISTER_TOP100_REALTIME='1'
& '$Python32' .\stockboard_server.py
"@

$serverWindow = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList "-NoExit", "-Command", $serverCommand `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Normal `
    -PassThru

Write-Host "Server window PID: $($serverWindow.Id)"
Write-Host "Realtime env: STOCKBOARD_ENABLE_COM_REALTIME=1, STOCKBOARD_REGISTER_TOP100_REALTIME=1"

Write-Step "Waiting for /api/top100"
$ready = $false
$lastError = ""
$top100Response = $null
$deadline = (Get-Date).AddMinutes(4)

while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    try {
        $top100Response = Invoke-RestMethod $Top100Url -TimeoutSec 10
        $ready = $true
        break
    } catch {
        $lastError = $_.Exception.Message
        Write-Host "Waiting... $lastError"
    }
}

if (-not $ready) {
    Fail "/api/top100 was not ready within 4 minutes. Last error: $lastError"
}

$rows = Get-RowsFromResponse $top100Response
$rowsCount = $rows.Count
Write-Host "TOP100 rows: $rowsCount" -ForegroundColor Green

Write-Step "Checking candidate fields"
$candidateScoreCount = @($rows | Where-Object { $null -ne $_.candidate_score }).Count
$candidateScoreMaxCount = @($rows | Where-Object { $null -ne $_.candidate_score_max }).Count
$candidateGradeTextCount = @($rows | Where-Object { $null -ne $_.candidate_grade_text }).Count
$isCandidateTrueCount = @($rows | Where-Object { $_.is_candidate -eq $true }).Count
$candidateRankCount = @($rows | Where-Object { $null -ne $_.candidate_rank }).Count
$momentumCount = @($rows | Where-Object { $null -ne $_.momentum }).Count
$candidateRanks = @($rows | Where-Object { $_.is_candidate -eq $true } | Sort-Object candidate_rank | Select-Object -ExpandProperty candidate_rank)
$scoreMaxValues = @($rows | Where-Object { $null -ne $_.candidate_score_max } | Select-Object -ExpandProperty candidate_score_max -Unique)

Write-Host "candidate_score_count=$candidateScoreCount"
Write-Host "candidate_score_max_count=$candidateScoreMaxCount"
Write-Host "candidate_grade_text_count=$candidateGradeTextCount"
Write-Host "is_candidate_true_count=$isCandidateTrueCount"
Write-Host "candidate_rank_count=$candidateRankCount"
Write-Host "momentum_count=$momentumCount"
Write-Host "candidate_ranks=$($candidateRanks -join ',')"
Write-Host "candidate_score_max_values=$($scoreMaxValues -join ',')"

Require-True ($isCandidateTrueCount -eq 5) "Expected exactly 5 candidates, got $isCandidateTrueCount."
Require-True (($candidateRanks -join ",") -eq "1,2,3,4,5") "Expected candidate_rank 1,2,3,4,5."
Require-True (-not ($scoreMaxValues -contains 2080 -or $scoreMaxValues -contains 2080.0)) "candidate_score_max is 2080; expected 100."
Require-True ($scoreMaxValues -contains 100 -or $scoreMaxValues -contains 100.0) "candidate_score_max does not include 100."

Write-Step "Checking realtime provider"
$provider = $null
$providerReady = $false
$providerDeadline = (Get-Date).AddMinutes(2)

while ((Get-Date) -lt $providerDeadline) {
    $provider = Invoke-RestMethod $ProviderStatusUrl -TimeoutSec 10
    $providerReady = (
        $provider.running -eq $true -and
        $provider.login_state -eq "connected" -and
        [int]$provider.registered_count -gt 0 -and
        $provider.realreg_succeeded -eq $true -and
        $provider.orderbook_realreg_succeeded -eq $true
    )

    if ($providerReady) {
        break
    }

    Write-Host (
        "Waiting provider... running={0} login_state={1} registered_count={2} realreg={3} orderbook={4}" -f
        $provider.running,
        $provider.login_state,
        $provider.registered_count,
        $provider.realreg_succeeded,
        $provider.orderbook_realreg_succeeded
    )
    Start-Sleep -Seconds 3
}

Write-Host "running=$($provider.running)"
Write-Host "login_state=$($provider.login_state)"
Write-Host "registered_count=$($provider.registered_count)"
Write-Host "realreg_succeeded=$($provider.realreg_succeeded)"
Write-Host "orderbook_realreg_succeeded=$($provider.orderbook_realreg_succeeded)"
Write-Host "realdata_received_count=$($provider.realdata_received_count)"
Write-Host "last_error=$($provider.last_error)"

Require-True ($provider.running -eq $true) "Realtime provider is not running."
Require-True ($provider.login_state -eq "connected") "Realtime provider login_state is not connected."
Require-True ([int]$provider.registered_count -gt 0) "Realtime provider registered_count is not greater than 0."
Require-True ($provider.realreg_succeeded -eq $true) "Realtime SetRealReg did not succeed."
Require-True ($provider.orderbook_realreg_succeeded -eq $true) "Realtime orderbook SetRealReg did not succeed."

Write-Step "Checking realtime status and patch"
$rt1 = Invoke-RestMethod $RealtimeStatusUrl -TimeoutSec 10
Start-Sleep -Seconds 3
$rt2 = Invoke-RestMethod $RealtimeStatusUrl -TimeoutSec 10
$patch = Invoke-RestMethod $RealtimePatchUrl -TimeoutSec 10

$sequenceMoved = ([int64]$rt2.sequence -ge [int64]$rt1.sequence)
Write-Host "realtime_sequence_1=$($rt1.sequence)"
Write-Host "realtime_sequence_2=$($rt2.sequence)"
Write-Host "quote_count=$($rt2.quote_count)"
Write-Host "realtime_patch_sequence=$($patch.sequence)"
Write-Host "realtime_patch_rows=$($patch.rows.Count)"

Require-True ($sequenceMoved) "Realtime sequence check failed."
Require-True ([int]$rt2.quote_count -gt 0) "Realtime quote_count is not greater than 0."
if ([int]$patch.rows.Count -le 0) {
    Write-Host "Realtime patch rows are currently 0. This can be delayed by market/session state; sequence=$($patch.sequence), quote_count=$($rt2.quote_count)." -ForegroundColor Yellow
}

Write-Step "Opening StockBoard"
$browserOpened = $false
try {
    Start-Process $BoardUrl
    $browserOpened = $true
    Write-Host "Opened: $BoardUrl" -ForegroundColor Green
} catch {
    Write-Host "Browser open failed: $($_.Exception.Message)" -ForegroundColor Yellow
}

Start-Sleep -Seconds 1
$serverPids = Get-ListenPid8000
$serverPid = if ($serverPids) { ($serverPids -join ",") } else { "unknown" }

Write-Step "Summary"
Write-Host "Server PID: $serverPid"
Write-Host "Python path: $pythonPath"
Write-Host "32-bit: $($pythonBitness -eq '32bit')"
Write-Host "/api/top100 rows: $rowsCount"
Write-Host "candidate_score_count: $candidateScoreCount"
Write-Host "is_candidate_true_count: $isCandidateTrueCount"
Write-Host "realtime provider running: $($provider.running)"
Write-Host "login_state: $($provider.login_state)"
Write-Host "registered_count: $($provider.registered_count)"
Write-Host "realtime_patch rows: $($patch.rows.Count)"
Write-Host "browser opened: $browserOpened"
Write-Host ""
Write-Host "Done. Keep the visible server PowerShell window open while using StockBoard." -ForegroundColor Green
