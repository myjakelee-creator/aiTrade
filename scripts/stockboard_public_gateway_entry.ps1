[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "status", "publish", "unpublish", "stop")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$CoreScript = Join-Path $PSScriptRoot "stockboard_public_gateway.ps1"

# Windows PowerShell 5.1 can throw "Argument types do not match" when the
# core launcher's empty Generic.List[object] fallback is wrapped in @(...).
# Keep the core logic unchanged and provide a predictable command-compatible
# listener source backed by netstat. The child script resolves this function
# before the NetTCPIP cmdlet and therefore never enters the problematic path.
function Get-NetTCPConnection {
    [CmdletBinding()]
    param(
        [int[]]$LocalPort,
        [string[]]$State
    )

    $wantedPorts = @($LocalPort | Where-Object { $_ -gt 0 })
    if ($wantedPorts.Count -eq 0) {
        return
    }

    $listenOnly = @($State) -contains "Listen"
    foreach ($line in @(netstat -ano -p tcp 2>$null)) {
        $match = [regex]::Match(
            [string]$line,
            '^\s*TCP\s+(\S+):(\d+)\s+\S+\s+(\S+)\s+(\d+)\s*$'
        )
        if (-not $match.Success) {
            continue
        }

        $portNumber = 0
        $processId = 0
        if (-not [int]::TryParse($match.Groups[2].Value, [ref]$portNumber)) {
            continue
        }
        if (-not [int]::TryParse($match.Groups[4].Value, [ref]$processId)) {
            continue
        }
        if ($wantedPorts -notcontains $portNumber) {
            continue
        }

        $netState = [string]$match.Groups[3].Value
        if ($listenOnly -and $netState -ne "LISTENING") {
            continue
        }

        [pscustomobject]@{
            LocalAddress = [string]$match.Groups[1].Value
            LocalPort = $portNumber
            OwningProcess = $processId
            State = if ($netState -eq "LISTENING") { "Listen" } else { $netState }
        }
    }
}

if (-not (Test-Path -LiteralPath $CoreScript)) {
    Write-Host "ERROR_MESSAGE=Public gateway core script was not found." -ForegroundColor Red
    Write-Host "ERROR_SCRIPT=$CoreScript" -ForegroundColor Red
    exit 1
}

try {
    & $CoreScript -Action $Action
    exit 0
} catch {
    $record = $_
    Write-Host "" 
    Write-Host "== StockBoard public gateway error detail ==" -ForegroundColor Red
    Write-Host "ERROR_MESSAGE=$($record.Exception.Message)" -ForegroundColor Red
    Write-Host "ERROR_SCRIPT=$($record.InvocationInfo.ScriptName)" -ForegroundColor Red
    Write-Host "ERROR_LINE=$($record.InvocationInfo.ScriptLineNumber)" -ForegroundColor Red
    Write-Host "ERROR_POSITION=$($record.InvocationInfo.PositionMessage)" -ForegroundColor Red
    Write-Host "ERROR_STACK=$($record.ScriptStackTrace)" -ForegroundColor Red
    exit 1
}
