$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$SnapshotUrl = "http://127.0.0.1:8765/api/v2/snapshot?limit=100"
$ReportPath = Join-Path $RuntimeDir "large_doctor_report.txt"

if (-not (Test-Path -LiteralPath $RuntimeDir)) {
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
}

$lines = New-Object System.Collections.Generic.List[string]
function Add-Line([string]$Text) {
    $lines.Add($Text) | Out-Null
    Write-Host $Text
}

function Display-Value($Value) {
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return "-"
    }
    return [string]$Value
}

try {
    $snapshot = Invoke-RestMethod -Uri $SnapshotUrl -TimeoutSec 5
} catch {
    Add-Line ""
    Add-Line "== FID228 / 5-minute strength diagnostics =="
    Add-Line "EXECUTION_DIAG_ERROR=$($_.Exception.Message)"
    Add-Content -LiteralPath $ReportPath -Value $lines -Encoding UTF8
    throw
}

$status = $snapshot.status
$diag = $snapshot.execution_strength_diagnostics
if ($null -eq $diag) {
    Add-Line ""
    Add-Line "== FID228 / 5-minute strength diagnostics =="
    Add-Line "EXECUTION_DIAG_ERROR=worker diagnostics not installed"
    Add-Line "EXECUTION_DIAG_EXPECTED_VERSION=execution_strength_diagnostics_v1"
    Add-Content -LiteralPath $ReportPath -Value $lines -Encoding UTF8
    exit 2
}

Add-Line ""
Add-Line "== FID228 / 5-minute strength diagnostics =="
Add-Line "EXECUTION_DIAG_TIME=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Line "MARKET_PHASE=$(Display-Value $status.market_phase)"
Add-Line "MARKET_PHASE_LABEL=$(Display-Value $status.market_phase_label)"
Add-Line "EXPECTED_TRADING_DATE=$(Display-Value $diag.expected_trading_date)"
Add-Line "EXECUTION_DIAG_VERSION=$(Display-Value $diag.version)"
Add-Line "EXECUTION_ROW_COUNT=$(Display-Value $diag.row_count)"
Add-Line "EXECUTION_POSITIVE_COUNT=$(Display-Value $diag.execution_positive_count)"
Add-Line "EXECUTION_TRUSTED_FID228_COUNT=$(Display-Value $diag.execution_trusted_fid228_count)"
Add-Line "EXECUTION_UNTRUSTED_POSITIVE_COUNT=$(Display-Value $diag.execution_untrusted_positive_count)"
Add-Line "EXECUTION_SOURCE_DATE_MISMATCH_COUNT=$(Display-Value $diag.execution_source_date_mismatch_count)"
Add-Line "EXECUTION_VISIBLE_COUNT=$(Display-Value $diag.execution_visible_count)"
Add-Line "STRENGTH5_VISIBLE_COUNT=$(Display-Value $diag.strength5_visible_count)"
Add-Line "EXECUTION_STRENGTH5_SAME_VALUE_COUNT=$(Display-Value $diag.execution_strength5_same_value_count)"
Add-Line "LAST_FID228_RECEIVED_AT=$(Display-Value $diag.last_fid228_received_at)"
Add-Line "EXECUTION_HIDDEN_DATE_COUNT=$(Display-Value $diag.execution_hidden_date_count)"
Add-Line "EXECUTION_HIDDEN_SOURCE_COUNT=$(Display-Value $diag.execution_hidden_source_count)"
Add-Line "EXECUTION_HIDDEN_STALE_COUNT=$(Display-Value $diag.execution_hidden_stale_count)"
Add-Line "EXECUTION_WS_STATUS=$(Display-Value $diag.websocket_status)"
Add-Line "EXECUTION_WS_SELECTED_COUNT=$(Display-Value $diag.websocket_selected_count)"
Add-Line "COLLECTOR_Q=$(Display-Value $diag.collector_queue)"
Add-Line "WORKER_Q=$(Display-Value $diag.worker_queue)"
Add-Line "DROP_COUNT=$(Display-Value $diag.drop_count)"
Add-Line "LOGDROP_COUNT=$(Display-Value $diag.logdrop_count)"
Add-Line "EXECUTION_SOURCE_CONTRACT_OK=$(Display-Value $diag.source_contract_ok)"

$result = "WAITING_FID228"
if ($diag.source_contract_ok -eq $false) {
    $result = "SOURCE_CONTRACT_FAIL"
} elseif ([int]($diag.execution_trusted_fid228_count) -gt 0) {
    $result = "FID228_VISIBLE"
} elseif ([string]$status.market_phase -in @("holiday", "weekend", "closed", "before_market")) {
    $result = "NO_NEW_TRADE_EXPECTED"
}
Add-Line "EXECUTION_DIAG_RESULT=$result"

Add-Line "EXECUTION_DIAG_SAMPLES_BEGIN"
foreach ($sample in @($diag.samples)) {
    Add-Line (
        "SAMPLE rank={0} code={1} name={2} execution={3} exec_source={4} exec_date={5} exec_at={6} strength5={7} strength5_source={8} strength5_date={9}" -f
        (Display-Value $sample.rank),
        (Display-Value $sample.stock_code),
        (Display-Value $sample.stock_name),
        (Display-Value $sample.execution_strength),
        (Display-Value $sample.execution_strength_source),
        (Display-Value $sample.execution_source_trading_date),
        (Display-Value $sample.execution_strength_observed_at),
        (Display-Value $sample.strength_5m),
        (Display-Value $sample.strength_source),
        (Display-Value $sample.strength_source_trading_date)
    )
}
Add-Line "EXECUTION_DIAG_SAMPLES_END"

Add-Content -LiteralPath $ReportPath -Value $lines -Encoding UTF8
Write-Host "EXECUTION_DIAG_REPORT=$ReportPath" -ForegroundColor Cyan
