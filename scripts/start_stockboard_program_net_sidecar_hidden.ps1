$ErrorActionPreference = 'Stop'

$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
$RuntimeDir = Join-Path $Root 'data\runtime'
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

$PidFile = Join-Path $RuntimeDir 'stockboard_program_net_sidecar.pid'
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$StdoutLog = Join-Path $RuntimeDir "stockboard_program_net_sidecar_$Stamp.out.log"
$StderrLog = Join-Path $RuntimeDir "stockboard_program_net_sidecar_$Stamp.err.log"
$ScriptPath = Join-Path $Root 'scripts\stockboard_program_net_snapshot.py'

function Stop-ExistingSidecar {
    if (-not (Test-Path $PidFile)) { return }
    $rawPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $oldPid = 0
    if (-not [int]::TryParse([string]$rawPid, [ref]$oldPid)) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        return
    }
    $process = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        try {
            Stop-Process -Id $oldPid -Force -ErrorAction Stop
            Start-Sleep -Milliseconds 300
        } catch {
            Write-Warning "failed to stop old program net sidecar pid=${oldPid}: $_"
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $ScriptPath)) {
    throw "sidecar script not found: $ScriptPath"
}

$Python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Python) {
    throw 'python command was not found in PATH'
}

Stop-ExistingSidecar

$Arguments = @(
    'scripts\stockboard_program_net_snapshot.py',
    '--interval',
    '60'
)

$process = Start-Process `
    -FilePath $Python `
    -ArgumentList $Arguments `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -PassThru

Set-Content -Path $PidFile -Value $process.Id -Encoding ASCII

Write-Host "PROGRAM_NET_SIDECAR_PID=$($process.Id)"
Write-Host "PROGRAM_NET_SIDECAR_STDOUT=$StdoutLog"
Write-Host "PROGRAM_NET_SIDECAR_STDERR=$StderrLog"
Write-Host "PROGRAM_NET_SIDECAR_PID_FILE=$PidFile"
