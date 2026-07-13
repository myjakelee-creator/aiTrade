$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\aiTrade"
$RuntimeDir = Join-Path $ProjectRoot "data\runtime\stockboard_v2"
$CollectorPidFile = Join-Path $RuntimeDir "collector32.pid"

Set-Location -LiteralPath $ProjectRoot

function Get-StockBoardCollectorRows {
    try {
        return @(
            Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    $commandLine = [string]$_.CommandLine
                    $commandLine -and (
                        $commandLine -like "*realtime_v2\collector32_large_bidask.py*" -or
                        $commandLine -like "*realtime_v2\collector32_large.py*" -or
                        $commandLine -like "*realtime_v2\collector32.py*"
                    )
                } |
                Select-Object ProcessId, Name, CommandLine
        )
    } catch {
        return @()
    }
}

function Get-OpstarterRows {
    try {
        return @(
            Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    $name = [string]$_.Name
                    $commandLine = [string]$_.CommandLine
                    $name -match '^(?i)opstarter.*\.exe$' -or
                        $commandLine -match '(?i)opstarter'
                } |
                Select-Object ProcessId, Name, CommandLine
        )
    } catch {
        return @()
    }
}

function Get-OtherOpenApiPythonRows {
    try {
        return @(
            Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    $name = [string]$_.Name
                    $commandLine = [string]$_.CommandLine
                    if ($name -notmatch '^(?i)python(w)?\.exe$' -or -not $commandLine) {
                        return $false
                    }
                    return (
                        $commandLine -match '(?i)kiwoom_interface_32' -or
                        $commandLine -match '(?i)interfaces\\kiwoom' -or
                        $commandLine -match '(?i)khopenapi' -or
                        $commandLine -match '(?i)collector32'
                    )
                } |
                Select-Object ProcessId, Name, CommandLine
        )
    } catch {
        return @()
    }
}

$collectorRows = @(Get-StockBoardCollectorRows)
if ($collectorRows.Count -gt 0) {
    foreach ($row in $collectorRows) {
        Write-Host "COLLECTOR_STILL_RUNNING PID=$($row.ProcessId) NAME=$($row.Name)"
    }
    throw "OpenAPI preflight requires the old StockBoard collector to be stopped first."
}

$opstarterRows = @(Get-OpstarterRows)
Write-Host "OPSTARTER_PRESTART_COUNT=$($opstarterRows.Count)"
foreach ($row in $opstarterRows) {
    Write-Host "Stopping orphan opstarter before collector start PID=$($row.ProcessId) NAME=$($row.Name)"
    Stop-Process -Id ([int]$row.ProcessId) -Force -ErrorAction SilentlyContinue
}
if ($opstarterRows.Count -gt 0) {
    Start-Sleep -Milliseconds 1200
}

$remainingOpstarter = @(Get-OpstarterRows)
if ($remainingOpstarter.Count -gt 0) {
    foreach ($row in $remainingOpstarter) {
        Write-Host "OPSTARTER_REMAINING PID=$($row.ProcessId) NAME=$($row.Name)"
    }
    throw "An orphan opstarter process remains after pre-start cleanup."
}

$otherOpenApiRows = @(Get-OtherOpenApiPythonRows)
if ($otherOpenApiRows.Count -gt 0) {
    foreach ($row in $otherOpenApiRows) {
        Write-Host "OTHER_OPENAPI_HOST PID=$($row.ProcessId) NAME=$($row.Name)"
        Write-Host "COMMAND=$($row.CommandLine)"
    }
    throw "Another 32-bit Kiwoom/OpenAPI Python host is running. Stop it before starting StockBoard."
}

Remove-Item -LiteralPath $CollectorPidFile -Force -ErrorAction SilentlyContinue
Write-Host "OPENAPI_PRESTART_READY=True"
