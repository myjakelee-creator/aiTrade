param(
    [ValidateRange(1, 300)]
    [int]$Limit = 30,

    [string]$Codes = "",

    [string]$SnapshotUrl = "http://127.0.0.1:8765/api/v2/snapshot?limit=300"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"

function Get-FirstValue {
    param([object]$Object, [string[]]$Names)
    if ($null -eq $Object) { return $null }
    foreach ($name in $Names) {
        $property = $Object.PSObject.Properties[$name]
        if ($null -eq $property) { continue }
        $value = $property.Value
        if ($null -ne $value -and [string]$value -ne "") { return $value }
    }
    return $null
}

function Get-Number {
    param([object]$Value)
    if ($null -eq $Value -or [string]$Value -eq "") { return $null }
    $text = ([string]$Value).Replace(",", "").Trim()
    $number = 0.0
    if ([double]::TryParse(
        $text,
        [System.Globalization.NumberStyles]::Float,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$number
    )) { return $number }
    return $null
}

function Get-DateDigits {
    param([object]$Value)
    $digits = -join (([string]$Value).ToCharArray() | Where-Object { [char]::IsDigit($_) })
    if ($digits.Length -ge 8) { return $digits.Substring(0, 8) }
    return ""
}

function Get-AgeSec {
    param([object]$Value)
    if ($null -eq $Value -or [string]$Value -eq "") { return $null }
    $parsed = [datetimeoffset]::MinValue
    if ([datetimeoffset]::TryParse([string]$Value, [ref]$parsed)) {
        return [math]::Max(0, ([datetimeoffset]::Now - $parsed).TotalSeconds)
    }
    return $null
}

function Get-ContractState {
    param(
        [object]$Value,
        [object]$Source,
        [string]$GoodPattern,
        [string]$BadPattern,
        [string]$GoodLabel,
        [string]$BadLabel
    )
    if ($null -eq $Value -or [string]$Value -eq "") { return "MISSING" }
    $sourceText = [string]$Source
    if ($sourceText -match $GoodPattern) { return $GoodLabel }
    if ($sourceText -match $BadPattern) { return $BadLabel }
    return "UNKNOWN_SOURCE"
}

$payload = Invoke-RestMethod -Uri $SnapshotUrl -TimeoutSec 10
$rows = @($payload.rows)
$status = $payload.status
$collector = $status.collector_status
$sender = $collector.sender_stats

$requestedCodes = @(
    ([string]$Codes).Split(",", [System.StringSplitOptions]::RemoveEmptyEntries) |
        ForEach-Object { ([string]$_).Trim() } |
        Where-Object { $_ -match '^\d{6}$' }
)
if ($requestedCodes.Count -gt 0) {
    $codeSet = @{}
    foreach ($code in $requestedCodes) { $codeSet[$code] = $true }
    $rows = @($rows | Where-Object { $codeSet.ContainsKey([string]$_.stock_code) })
} else {
    $rows = @($rows | Select-Object -First $Limit)
}

$currentDate = Get-DateDigits (Get-FirstValue $status @(
    "board_display_current_trading_date", "board_expected_trading_date"
))
if (-not $currentDate) { $currentDate = Get-DateDigits $payload.trading_date }
$holdDate = Get-DateDigits (Get-FirstValue $status @(
    "board_display_hold_source_trading_date", "board_source_trading_date"
))

$result = foreach ($row in $rows) {
    $price = Get-Number (Get-FirstValue $row @("price", "trade_price"))
    $rate = Get-Number (Get-FirstValue $row @("change_rate", "realtime_change_rate"))
    $receivedAt = Get-FirstValue $row @(
        "price_received_at", "trade_received_at", "received_at", "last_trade_event_received_at"
    )
    $priceAge = Get-Number (Get-FirstValue $row @("price_age_sec"))
    if ($null -eq $priceAge) { $priceAge = Get-AgeSec $receivedAt }
    $receivedDate = Get-DateDigits $receivedAt

    $priceDate = Get-DateDigits (Get-FirstValue $row @("price_trading_date", "source_trading_date"))
    $tradeValueDate = Get-DateDigits (Get-FirstValue $row @("trade_value_trading_date", "source_trading_date"))
    $rowSource = [string](Get-FirstValue $row @("row_source", "source_code"))
    $sourceCode = [string](Get-FirstValue $row @("source_code", "registered_code"))
    $looksRealtime = $rowSource -match 'realtime' -or $sourceCode -match '^\d{6}(_AL|_NX)?$'

    $priceState = "UNKNOWN"
    $isCurrentRealtime = $looksRealtime -and (
        $priceDate -eq $currentDate -or $receivedDate -eq $currentDate -or (-not $priceDate -and $currentDate)
    )
    if ($isCurrentRealtime -and $null -ne $priceAge -and $priceAge -le 3) {
        $priceState = "LIVE_RECENT"
    } elseif ($isCurrentRealtime -and $null -ne $priceAge) {
        $priceState = "LIVE_NO_RECENT_TRADE"
    } elseif ($isCurrentRealtime) {
        $priceState = "LIVE_UNKNOWN_AGE"
    } elseif ($holdDate -and $priceDate -eq $holdDate -and $rowSource -match 'portable_exact_close') {
        $priceState = "HOLD"
    }

    $tradeValue = Get-Number (Get-FirstValue $row @("trade_value_eok"))
    $previousValue = Get-Number (Get-FirstValue $row @("prev_trade_value_eok"))
    $amountRatio = Get-Number (Get-FirstValue $row @("amount_ratio"))
    $calculatedRatio = $null
    $ratioDelta = $null
    if ($null -ne $tradeValue -and $null -ne $previousValue -and $previousValue -gt 0) {
        $calculatedRatio = $tradeValue / $previousValue
        if ($null -ne $amountRatio) { $ratioDelta = [math]::Abs($amountRatio - $calculatedRatio) }
    }

    $minuteValue = Get-FirstValue $row @(
        "trade_value_1m_eok", "minute_value_eok", "one_minute_trade_value_eok", "completed_1m_trade_value_eok"
    )

    $bidAsk = Get-FirstValue $row @("bid_ask_ratio")
    $bidAskSource = Get-FirstValue $row @("orderbook_source")
    $bidAskDate = Get-DateDigits (Get-FirstValue $row @("orderbook_source_trading_date"))
    $bidAskAge = Get-AgeSec (Get-FirstValue $row @("orderbook_received_at", "ui_orderbook_observed_at"))
    $bidAskContract = Get-ContractState $bidAsk $bidAskSource '0D|qax_realtime_orderbook|realtime_orderbook' '0C_rotating' 'OK_0D' 'WRONG_0C'

    $execution = Get-FirstValue $row @("execution_strength")
    $executionSource = Get-FirstValue $row @("execution_strength_source", "execution_source")
    $executionDate = Get-DateDigits (Get-FirstValue $row @("execution_source_trading_date"))
    $executionAge = Get-AgeSec (Get-FirstValue $row @(
        "execution_strength_received_at", "execution_strength_updated_at", "ui_execution_strength_observed_at"
    ))
    $executionContract = Get-ContractState $execution $executionSource '0B.*fid228|fid228.*0B' '0A.*fid228|fid228.*0A|ka10046' 'OK_0B_FID228' 'WRONG_0A'

    $strength5 = Get-FirstValue $row @("strength_5m", "strength5", "five_min_strength", "strength_5min")
    $strength5Source = Get-FirstValue $row @("strength_source")
    $strength5Date = Get-DateDigits (Get-FirstValue $row @("strength_source_trading_date"))
    $strength5Age = Get-AgeSec (Get-FirstValue $row @("strength_snapshot_at", "ui_strength_observed_at"))
    $strength5Contract = Get-ContractState $strength5 $strength5Source 'ka10046|opt10046' 'ka10045|opt10045' 'OK_KA10046' 'WRONG_KA10045'

    $program = Get-FirstValue $row @("program_net")
    $programSource = Get-FirstValue $row @("program_net_source")
    $programDate = Get-DateDigits (Get-FirstValue $row @("program_source_trading_date"))
    $programAge = Get-AgeSec (Get-FirstValue $row @("program_net_updated_at"))
    $programContract = Get-ContractState $program $programSource 'ka90004|program_ws_0u' 'ka90003' 'OK_KA90004' 'WRONG_KA90003'

    [pscustomobject]@{
        Rank = Get-FirstValue $row @("rank", "candidate_rank")
        Code = [string]$row.stock_code
        Name = [string]$row.stock_name
        Price = $price
        RatePct = $rate
        PriceState = $priceState
        LastTradeAgeSec = if ($null -eq $priceAge) { $null } else { [math]::Round($priceAge, 2) }
        PriceAgeSec = if ($null -eq $priceAge) { $null } else { [math]::Round($priceAge, 2) }
        PriceDate = $priceDate
        ReceivedDate = $receivedDate
        SourceCode = $sourceCode
        TradeValueEok = $tradeValue
        TradeValueDate = $tradeValueDate
        PrevValueEok = $previousValue
        AmountRatio = $amountRatio
        RatioDelta = if ($null -eq $ratioDelta) { $null } else { [math]::Round($ratioDelta, 4) }
        MinuteValueEok = $minuteValue
        BidAsk = $bidAsk
        BidAskContract = $bidAskContract
        BidAskSource = $bidAskSource
        BidAskDate = $bidAskDate
        BidAskAgeSec = if ($null -eq $bidAskAge) { $null } else { [math]::Round($bidAskAge, 1) }
        Execution = $execution
        ExecutionContract = $executionContract
        ExecutionSource = $executionSource
        ExecutionDate = $executionDate
        ExecutionAgeSec = if ($null -eq $executionAge) { $null } else { [math]::Round($executionAge, 1) }
        Strength5 = $strength5
        Strength5Contract = $strength5Contract
        Strength5Source = $strength5Source
        Strength5Date = $strength5Date
        Strength5AgeSec = if ($null -eq $strength5Age) { $null } else { [math]::Round($strength5Age, 1) }
        ProgramEok = $program
        ProgramContract = $programContract
        ProgramSource = $programSource
        ProgramDate = $programDate
        ProgramAgeSec = if ($null -eq $programAge) { $null } else { [math]::Round($programAge, 1) }
    }
}

$snapshotAge = Get-AgeSec $payload.ts
$collectorPending = Get-Number $sender.pending_total_count
$workerQueue = Get-Number $status.event_log_queue_size
$workerDrop = Get-Number $status.dropped_trade_count
$pipelineIssues = @()
if ($null -eq $snapshotAge -or $snapshotAge -gt 4) { $pipelineIssues += "snapshot_age" }
if ($null -ne $collectorPending -and $collectorPending -gt 100) { $pipelineIssues += "collector_queue" }
if ($null -ne $workerQueue -and $workerQueue -gt 100) { $pipelineIssues += "worker_queue" }
if ($null -ne $workerDrop -and $workerDrop -gt 0) { $pipelineIssues += "worker_drop" }
if ($null -ne $sender.connected -and -not [bool]$sender.connected) { $pipelineIssues += "collector_disconnected" }
$pipelineState = if ($pipelineIssues.Count -eq 0) { "HEALTHY" } else { "CHECK" }

$summary = [pscustomobject]@{
    Time = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    SnapshotAgeMs = if ($null -eq $snapshotAge) { $null } else { [math]::Round($snapshotAge * 1000, 0) }
    PipelineState = $pipelineState
    PipelineIssues = ($pipelineIssues -join ",")
    CurrentTradingDate = $currentDate
    HoldSourceDate = $holdDate
    ContinuityMode = $status.board_display_continuity_mode
    RowsInspected = @($result).Count
    Live = @($result | Where-Object PriceState -match '^LIVE_').Count
    LiveRecent = @($result | Where-Object PriceState -eq "LIVE_RECENT").Count
    NoRecentTrade = @($result | Where-Object PriceState -eq "LIVE_NO_RECENT_TRADE").Count
    LiveUnknownAge = @($result | Where-Object PriceState -eq "LIVE_UNKNOWN_AGE").Count
    Hold = @($result | Where-Object PriceState -eq "HOLD").Count
    Unknown = @($result | Where-Object PriceState -eq "UNKNOWN").Count
    RatioMismatch = @($result | Where-Object { $null -ne $_.RatioDelta -and $_.RatioDelta -gt 0.01 }).Count
    BidAskOK = @($result | Where-Object BidAskContract -eq "OK_0D").Count
    BidAskWrong = @($result | Where-Object BidAskContract -match '^WRONG').Count
    ExecutionOK = @($result | Where-Object ExecutionContract -eq "OK_0B_FID228").Count
    ExecutionWrong = @($result | Where-Object ExecutionContract -match '^WRONG').Count
    Strength5OK = @($result | Where-Object Strength5Contract -eq "OK_KA10046").Count
    Strength5Wrong = @($result | Where-Object Strength5Contract -match '^WRONG').Count
    ProgramOK = @($result | Where-Object ProgramContract -eq "OK_KA90004").Count
    ProgramWrong = @($result | Where-Object ProgramContract -match '^WRONG').Count
    CollectorPending = $sender.pending_total_count
    CollectorSentPerSec = $sender.sent_per_sec
    CollectorCoalesced = $sender.coalesced_trade_overwrite_count
    WorkerEventCount = $status.event_count
    WorkerTradeCount = $status.trade_count
    WorkerQueue = $status.event_log_queue_size
    WorkerDrop = $status.dropped_trade_count
    WorkerDropReasons = $status.dropped_trade_reason_counts
    TradeFieldGuardVersion = $status.trade_field_regression_guard_version
    TradeFieldSuppressed = $status.trade_field_regression_suppressed_count
    TradeFieldSuppressReasons = $status.trade_field_regression_suppressed_reason_counts
    DailyCumulativeResetAccepted = $status.daily_cumulative_reset_accepted_count
    DailyCumulativeResetReasons = $status.trade_field_regression_accepted_reason_counts
    LastDailyCumulativeReset = $status.last_daily_cumulative_reset_accepted
    SourceContractVersion = $status.aux_metric_source_contract_version
    ExecutionRealtimeType = $status.execution_realtime_type
    OrderbookRealtimeType = $status.orderbook_realtime_type
    StrengthApiId = $status.strength_tr_api_id
    ProgramApiId = $status.program_tr_api_id
    RealtimeStrengthStatus = $status.realtime_strength_ws_status
    RealtimeStrengthEvents = $status.realtime_strength_ws_event_count
    RealtimeStrengthError = $status.realtime_strength_ws_last_error
    RestMetricsStatus = $status.rest_live_metrics_status
    RestMetricsRequests = $status.rest_live_metrics_request_count
    RestMetricsSuccess = $status.rest_live_metrics_success_count
    RestMetricsErrors = $status.rest_live_metrics_error_count
    RestMetricsLastError = $status.rest_live_metrics_last_error
    ProgramLastError = $status.program_net_last_error
    ActivePayloadCache = $status.board_display_active_payload_cache_status
    ActivePayloadRetrySec = $status.board_display_active_payload_retry_sec
    ActivePayloadFileReads = $status.board_display_active_payload_file_read_count
    ActivePayloadLookupMs = $status.board_display_active_payload_last_lookup_ms
    ActiveApplyMs = $status.board_display_active_apply_ms
    OpeningOverlayMs = $status.opening_burst_cache_last_overlay_ms
}

Write-Host ""
Write-Host "=== StockBoard data consistency summary ===" -ForegroundColor Cyan
$summary | Format-List

Write-Host ""
Write-Host "=== Row-level source / freshness / consistency ===" -ForegroundColor Cyan
$result |
    Select-Object Rank, Code, Name, Price, RatePct, PriceState, LastTradeAgeSec,
        TradeValueEok, AmountRatio, RatioDelta,
        BidAsk, BidAskContract, Execution, ExecutionContract,
        Strength5, Strength5Contract, ProgramEok, ProgramContract |
    Format-Table -AutoSize

if (-not (Test-Path -LiteralPath $RuntimeDir)) {
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
}
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$csvPath = Join-Path $RuntimeDir "data_consistency_$stamp.csv"
$jsonPath = Join-Path $RuntimeDir "data_consistency_$stamp.json"
$result | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding UTF8
@{
    summary = $summary
    rows = @($result)
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

Write-Host ""
Write-Host "CSV_REPORT=$csvPath" -ForegroundColor Cyan
Write-Host "JSON_REPORT=$jsonPath" -ForegroundColor Cyan
