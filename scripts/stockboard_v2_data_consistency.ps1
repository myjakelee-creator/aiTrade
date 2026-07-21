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
    param(
        [object]$Object,
        [string[]]$Names
    )
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
    )) {
        return $number
    }
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

function Format-OptionalNumber {
    param([object]$Value, [int]$Digits = 2)
    $number = Get-Number $Value
    if ($null -eq $number) { return "-" }
    return $number.ToString("N$Digits")
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
    "board_display_current_trading_date",
    "board_expected_trading_date"
))
if (-not $currentDate) { $currentDate = Get-DateDigits $payload.trading_date }
$holdDate = Get-DateDigits (Get-FirstValue $status @(
    "board_display_hold_source_trading_date",
    "board_source_trading_date"
))

$result = foreach ($row in $rows) {
    $price = Get-Number (Get-FirstValue $row @("price", "trade_price"))
    $rate = Get-Number (Get-FirstValue $row @("change_rate", "realtime_change_rate"))
    $priceAge = Get-Number (Get-FirstValue $row @("price_age_sec"))
    if ($null -eq $priceAge) {
        $priceAge = Get-AgeSec (Get-FirstValue $row @(
            "price_received_at",
            "trade_received_at",
            "received_at"
        ))
    }

    $priceDate = Get-DateDigits (Get-FirstValue $row @(
        "price_trading_date",
        "source_trading_date"
    ))
    $rateDate = Get-DateDigits (Get-FirstValue $row @(
        "change_rate_trading_date",
        "source_trading_date"
    ))
    $tradeValueDate = Get-DateDigits (Get-FirstValue $row @(
        "trade_value_trading_date",
        "source_trading_date"
    ))

    $rowSource = [string](Get-FirstValue $row @("row_source", "source_code"))
    $priceState = "UNKNOWN"
    if ($priceDate -eq $currentDate -and $null -ne $priceAge -and $priceAge -le 3) {
        $priceState = "LIVE"
    } elseif ($holdDate -and $priceDate -eq $holdDate -and $rowSource -match "portable_exact_close") {
        $priceState = "HOLD"
    } elseif ($priceDate -eq $currentDate) {
        $priceState = "LIVE_STALE"
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
        "minute_value_eok",
        "trade_value_1m_eok",
        "one_minute_trade_value_eok",
        "completed_1m_trade_value_eok"
    )
    $bidAsk = Get-FirstValue $row @("bid_ask_ratio")
    $execution = Get-FirstValue $row @("execution_strength")
    $strength5 = Get-FirstValue $row @(
        "strength_5m",
        "strength5",
        "five_min_strength",
        "strength_5min"
    )
    $program = Get-FirstValue $row @("program_net")
    $largeTrade = Get-FirstValue $row @("large_trade_net_count")

    [pscustomobject]@{
        Rank = Get-FirstValue $row @("rank", "candidate_rank")
        Code = [string]$row.stock_code
        Name = [string]$row.stock_name
        Price = $price
        RatePct = $rate
        PriceState = $priceState
        PriceAgeSec = if ($null -eq $priceAge) { $null } else { [math]::Round($priceAge, 2) }
        PriceDate = $priceDate
        RateDate = $rateDate
        Source = $rowSource
        SourceCode = Get-FirstValue $row @("source_code", "registered_code")
        TradeValueEok = $tradeValue
        TradeValueDate = $tradeValueDate
        PrevValueEok = $previousValue
        AmountRatio = $amountRatio
        RatioDelta = if ($null -eq $ratioDelta) { $null } else { [math]::Round($ratioDelta, 4) }
        MinuteValueEok = $minuteValue
        BidAsk = $bidAsk
        BidAskDate = Get-DateDigits (Get-FirstValue $row @("orderbook_source_trading_date"))
        Execution = $execution
        ExecutionDate = Get-DateDigits (Get-FirstValue $row @("execution_source_trading_date"))
        ExecutionSource = Get-FirstValue $row @("execution_strength_source", "execution_source")
        Strength5 = $strength5
        Strength5Date = Get-DateDigits (Get-FirstValue $row @("strength_source_trading_date"))
        Strength5Source = Get-FirstValue $row @("strength_source")
        ProgramEok = $program
        ProgramDate = Get-DateDigits (Get-FirstValue $row @("program_source_trading_date"))
        ProgramSource = Get-FirstValue $row @("program_net_source")
        LargeTrade = $largeTrade
        LargeTradeDate = Get-DateDigits (Get-FirstValue $row @("large_trade_source_trading_date"))
        LargeTradeQuality = Get-FirstValue $row @("large_trade_quality")
    }
}

$snapshotAge = Get-AgeSec $payload.ts
$summary = [pscustomobject]@{
    Time = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    SnapshotAgeMs = if ($null -eq $snapshotAge) { $null } else { [math]::Round($snapshotAge * 1000, 0) }
    CurrentTradingDate = $currentDate
    HoldSourceDate = $holdDate
    ContinuityMode = $status.board_display_continuity_mode
    RowsInspected = @($result).Count
    Live = @($result | Where-Object PriceState -eq "LIVE").Count
    Hold = @($result | Where-Object PriceState -eq "HOLD").Count
    LiveStale = @($result | Where-Object PriceState -eq "LIVE_STALE").Count
    Unknown = @($result | Where-Object PriceState -eq "UNKNOWN").Count
    RatioMismatch = @($result | Where-Object { $null -ne $_.RatioDelta -and $_.RatioDelta -gt 0.01 }).Count
    BidAskPresent = @($result | Where-Object { $null -ne $_.BidAsk }).Count
    ExecutionPresent = @($result | Where-Object { $null -ne $_.Execution }).Count
    Strength5Present = @($result | Where-Object { $null -ne $_.Strength5 }).Count
    ProgramPresent = @($result | Where-Object { $null -ne $_.ProgramEok }).Count
    CollectorPending = $sender.pending_total_count
    CollectorSentPerSec = $sender.sent_per_sec
    CollectorCoalesced = $sender.coalesced_trade_overwrite_count
    WorkerEventCount = $status.event_count
    WorkerTradeCount = $status.trade_count
    WorkerLogQueue = $status.event_log_queue_size
    WorkerLogDrop = $status.event_log_dropped_count
    ActivePayloadCache = $status.board_display_active_payload_cache_status
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
    Select-Object Rank, Code, Name, Price, RatePct, PriceState, PriceAgeSec, PriceDate, SourceCode,
        TradeValueEok, AmountRatio, RatioDelta, BidAsk, Execution, Strength5, ProgramEok, LargeTrade |
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
