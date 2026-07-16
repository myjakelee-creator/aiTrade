$ErrorActionPreference = "Stop"

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

function Test-WebSocketDependency([string]$Python64) {
    & $Python64 -c "from websockets.sync.client import connect; print('websockets_sync_ok')" 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

$python64 = Resolve-Python64
Write-Host "STOCKBOARD_PYTHON64=$python64"

if (-not (Test-WebSocketDependency $python64)) {
    Write-Host "Installing missing StockBoard WebSocket dependency..." -ForegroundColor Yellow
    & $python64 -m pip install --disable-pip-version-check "websockets>=14,<17"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install the 64-bit Python 'websockets' package."
    }
}

if (-not (Test-WebSocketDependency $python64)) {
    throw "The 64-bit Python WebSocket dependency is still unavailable after installation."
}

Write-Host "STOCKBOARD_WEBSOCKET_DEPENDENCY_READY=True"
