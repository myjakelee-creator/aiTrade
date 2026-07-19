$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$PidFile = Join-Path $RuntimeDir "context_snapshot_writer.pid"
$StatusFile = Join-Path $RuntimeDir "context_snapshot_status.json"
$OwnerStatusFile = Join-Path $RuntimeDir "context_owner_status.json"
$ExpectedOwner = "tr_singleflight"
$ExpectedRuntime = "singleflight_explicit_loop_v3"
$ModuleName = "realtime_v2.context_snapshot_writer_portable_v2"
$ExpectedEntrypoint = "realtime_v2.context_snapshot_writer_portable_v2"
$ExpectedParser = "exact_daily_row_fields_v2"
$ContextWriterPattern = '(?i)realtime_v2[\\.]context_snapshot_writer(?:_(?:base|singleflight|portable(?:_v2)?))?(?:\.py)?'

Set-Location -LiteralPath $ProjectRoot

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

function Get-ContextWriterRows {
    try {
        return @(
            Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    $name = [string]$_.Name
                    $commandLine = [string]$_.CommandLine
                    if ($name -notmatch '^(?i)python(w)?\.exe$' -or -not $commandLine) {
                        return $false
                    }
                    return $commandLine -match $ContextWriterPattern
                } |
                Select-Object ProcessId, Name, CommandLine
        )
    } catch {
        return @()
    }
}

function Stop-ContextWriters {
    foreach ($row in @(Get-ContextWriterRows)) {
        $pidNumber = [int]$row.ProcessId
        if ($pidNumber -gt 0 -and $pidNumber -ne $PID) {
            Write-Host "Stopping context writer PID=$pidNumber"
            Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 500
}

function Write-OwnerStatus(
    [System.Collections.IEnumerable]$Rows,
    [int]$OwnerPid,
    [bool]$Ready,
    [string]$Reason
) {
    $rowsArray = @($Rows)
    $legacyRows = @(
        $rowsArray | Where-Object {
            ([string]$_.CommandLine) -notmatch [regex]::Escape($ModuleName)
        }
    )
    $payload = [ordered]@{
        schema_version = 1
        source = "stockboard_context_single_owner_launcher"
        ts = (Get-Date).ToString("o")
        ready = $Ready
        reason = $Reason
        context_writer_process_count = $rowsArray.Count
        context_writer_owner_pid = $OwnerPid
        context_writer_owner_module = $ModuleName
        legacy_context_writer_detected = ($legacyRows.Count -gt 0)
        process_ids = @($rowsArray | ForEach-Object { [int]$_.ProcessId })
        command_lines = @($rowsArray | ForEach-Object { [string]$_.CommandLine })
    }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $OwnerStatusFile -Encoding UTF8
    return $payload
}

function Show-LogTail([string]$Path, [string]$Label) {
    Write-Host "---- $Label ----" -ForegroundColor Yellow
    Get-Content -LiteralPath $Path -Tail 120 -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
Stop-ContextWriters
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $StatusFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $OwnerStatusFile -Force -ErrorAction SilentlyContinue

$python64 = Resolve-Python64
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdout = Join-Path $RuntimeDir "context_singleflight_$stamp.out.log"
$stderr = Join-Path $RuntimeDir "context_singleflight_$stamp.err.log"

Write-Host "CONTEXT_SINGLEFLIGHT_PYTHON=$python64"
Write-Host "CONTEXT_SINGLEFLIGHT_MODULE=$ModuleName"

$process = Start-Process `
    -FilePath $python64 `
    -ArgumentList @("-m", $ModuleName, "--interval-sec", "30", "--ohlc-bootstrap") `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru

Set-Content -LiteralPath $PidFile -Value $process.Id -Encoding ASCII
Write-Host "CONTEXT_SINGLEFLIGHT_PID=$($process.Id)"
Write-Host "CONTEXT_SINGLEFLIGHT_STDOUT=$stdout"
Write-Host "CONTEXT_SINGLEFLIGHT_STDERR=$stderr"

$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    $alive = $null -ne (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)
    if (-not $alive) {
        $rows = @(Get-ContextWriterRows)
        [void](Write-OwnerStatus $rows $process.Id $false "owner_exited_before_readiness")
        Show-LogTail $stdout "context stdout"
        Show-LogTail $stderr "context stderr"
        throw "Context single-flight writer exited before readiness."
    }

    if (Test-Path -LiteralPath $StatusFile) {
        try {
            $status = Get-Content -LiteralPath $StatusFile -Raw | ConvertFrom-Json
            $owner = [string]$status.context_owner
            $runtime = [string]$status.context_runtime_version
            $entrypoint = [string]$status.context_entrypoint
            $parser = [string]$status.portable_board_parser_version
            $statusPid = [int]$status.context_process_pid
            $rows = @(Get-ContextWriterRows)
            $ownerRows = @($rows | Where-Object { [int]$_.ProcessId -eq [int]$process.Id })
            $legacyRows = @(
                $rows | Where-Object {
                    ([string]$_.CommandLine) -notmatch [regex]::Escape($ModuleName)
                }
            )
            $singleOwner = (
                $rows.Count -eq 1 -and
                $ownerRows.Count -eq 1 -and
                $legacyRows.Count -eq 0
            )
            if (
                $owner -eq $ExpectedOwner -and
                $runtime -eq $ExpectedRuntime -and
                $entrypoint -eq $ExpectedEntrypoint -and
                $parser -eq $ExpectedParser -and
                $statusPid -eq [int]$process.Id -and
                $singleOwner
            ) {
                $ownerStatus = Write-OwnerStatus $rows $process.Id $true "ready"
                Write-Host "CONTEXT_SINGLEFLIGHT_READY=True pid=$statusPid owner=$owner runtime=$runtime"
                Write-Host "CONTEXT_WRITER_PROCESS_COUNT=$($ownerStatus.context_writer_process_count)"
                Write-Host "CONTEXT_WRITER_OWNER_MODULE=$($ownerStatus.context_writer_owner_module)"
                Write-Host "LEGACY_CONTEXT_WRITER_DETECTED=$($ownerStatus.legacy_context_writer_detected)"
                exit 0
            }
        } catch { }
    }
    Start-Sleep -Milliseconds 250
}

$rows = @(Get-ContextWriterRows)
[void](Write-OwnerStatus $rows $process.Id $false "readiness_timeout_or_multiple_owners")
Write-Host "CONTEXT_WRITER_PROCESS_COUNT=$($rows.Count)" -ForegroundColor Yellow
foreach ($row in $rows) {
    Write-Host "CONTEXT_WRITER PID=$($row.ProcessId) COMMAND=$($row.CommandLine)" -ForegroundColor Yellow
}
Show-LogTail $stdout "context stdout"
Show-LogTail $stderr "context stderr"
throw "Context portable-v2 single-owner readiness was not confirmed within 30 seconds."
