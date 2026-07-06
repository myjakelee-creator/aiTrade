$ErrorActionPreference = 'SilentlyContinue'

$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
$RuntimeDir = Join-Path $Root 'data\runtime'
$PidFile = Join-Path $RuntimeDir 'stockboard_program_net_sidecar.pid'

if (-not (Test-Path $PidFile)) {
    Write-Host 'PROGRAM_NET_SIDECAR_RUNNING=False'
    Write-Host 'PROGRAM_NET_SIDECAR_STOPPED=0'
    exit 0
}

$rawPid = (Get-Content $PidFile | Select-Object -First 1)
$pidNumber = 0
if (-not [int]::TryParse([string]$rawPid, [ref]$pidNumber)) {
    Remove-Item $PidFile -Force
    Write-Host 'PROGRAM_NET_SIDECAR_RUNNING=False'
    Write-Host 'PROGRAM_NET_SIDECAR_STOPPED=0'
    exit 0
}

$process = Get-Process -Id $pidNumber
if ($null -ne $process) {
    Stop-Process -Id $pidNumber -Force
    Write-Host 'PROGRAM_NET_SIDECAR_RUNNING=True'
    Write-Host 'PROGRAM_NET_SIDECAR_STOPPED=1'
    Write-Host "PROGRAM_NET_SIDECAR_PID=$pidNumber"
} else {
    Write-Host 'PROGRAM_NET_SIDECAR_RUNNING=False'
    Write-Host 'PROGRAM_NET_SIDECAR_STOPPED=0'
}

Remove-Item $PidFile -Force
