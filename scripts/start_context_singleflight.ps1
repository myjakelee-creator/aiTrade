$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$PidFile = Join-Path $RuntimeDir "context_snapshot_writer.pid"
$StatusFile = Join-Path $RuntimeDir "context_snapshot_status.json"
$ExpectedOwner = "tr_singleflight"
$ExpectedRuntime = "singleflight_explicit_loop_v3"
$ModuleName = "realtime_v2.context_snapshot_writer_portable"

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
                    return $commandLine -match '(?i)realtime_v2[\\.]context_snapshot_writer(_base|_singleflight|_portable)?(\.py)?'
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

function Show-LogTail([string]$Path, [string]$Label) {
    Write-Host "---- $Label ----" -ForegroundColor Yellow
    Get-Content -LiteralPath $Path -Tail 120 -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
Stop-ContextWriters
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $StatusFile -Force -ErrorAction SilentlyContinue

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

$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    $alive = $null -ne (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)
    if (-not $alive) {
        Show-LogTail $stdout "context stdout"
        Show-LogTail $stderr "context stderr"
        throw "Context single-flight writer exited before readiness."
    }

    if (Test-Path -LiteralPath $StatusFile) {
        try {
            $status = Get-Content -LiteralPath $StatusFile -Raw | ConvertFrom-Json
            $owner = [string]$status.context_owner
            $runtime = [string]$status.context_runtime_version
            $statusPid = [int]$status.context_process_pid
            if (
                $owner -eq $ExpectedOwner -and
                $runtime -eq $ExpectedRuntime -and
                $statusPid -eq [int]$process.Id
            ) {
                Write-Host "CONTEXT_SINGLEFLIGHT_READY=True pid=$statusPid owner=$owner runtime=$runtime"
                exit 0
            }
        } catch { }
    }
    Start-Sleep -Milliseconds 250
}

Show-LogTail $stdout "context stdout"
Show-LogTail $stderr "context stderr"
throw "Context single-flight readiness was not confirmed within 20 seconds."
