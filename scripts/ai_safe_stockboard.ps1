param(
    [ValidateSet("status", "check")]
    [string]$Command = "status"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$Args)

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & git @Args
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "git $($Args -join ' ') failed with exit code $exitCode"
    }
}

function Get-CurrentBranch {
    $branch = (& git branch --show-current 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($branch)) {
        throw "Unable to determine current git branch."
    }
    return $branch
}

function Get-ChangedPaths {
    $paths = @()
    $statusLines = & git status --porcelain=v1
    if ($LASTEXITCODE -ne 0) {
        throw "git status --porcelain=v1 failed."
    }
    foreach ($line in $statusLines) {
        if ($line.Length -ge 4) {
            $path = $line.Substring(3).Trim()
            if ($path -match " -> ") {
                $path = ($path -split " -> ")[-1]
            }
            if ($path) {
                $paths += $path.Replace("\", "/")
            }
        }
    }
    return $paths
}

function Test-BlockedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $normalized = $Path.Replace("\", "/")
    return (
        $normalized -like "data/runtime/*" -or
        $normalized -like "data/execution_charts/*" -or
        $normalized -eq ".env" -or
        $normalized -match "(?i)(credential|token|secret|account)" -or
        $normalized -match "(?i)\.(pem|key|pfx|p12)$"
    )
}

function Assert-NoTrackedBlockedPaths {
    $tracked = & git ls-files
    if ($LASTEXITCODE -ne 0) {
        throw "git ls-files failed."
    }
    $blocked = @($tracked | Where-Object { Test-BlockedPath $_ })
    if ($blocked.Count -gt 0) {
        throw "Blocked paths are tracked by git:`n$($blocked -join "`n")"
    }
}

function Assert-NoChangedBlockedPaths {
    $changed = @(Get-ChangedPaths)
    $blocked = @($changed | Where-Object { Test-BlockedPath $_ })
    if ($blocked.Count -gt 0) {
        throw "Blocked runtime/secret paths are changed or staged:`n$($blocked -join "`n")"
    }
}

function Invoke-PythonCompile {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        $python = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $python) {
        throw "Python executable not found for py_compile check."
    }

    $pyFiles = @(& git ls-files "*.py")
    if ($LASTEXITCODE -ne 0) {
        throw "git ls-files *.py failed."
    }
    if ($pyFiles.Count -eq 0) {
        Write-Output "py_compile: no tracked Python files"
        return
    }

    & $python.Source -m py_compile @pyFiles
    if ($LASTEXITCODE -ne 0) {
        throw "py_compile failed."
    }
    Write-Output "py_compile: ok ($($pyFiles.Count) files)"
}

function Show-Status {
    $branch = Get-CurrentBranch
    Write-Output "Command: $Command"
    Write-Output "Branch: $branch"
    Write-Output "Intended action: report repository safety status only"
    Write-Output ""
    Write-Output "Changed files:"
    $status = & git status --short
    if ($LASTEXITCODE -ne 0) {
        throw "git status --short failed."
    }
    if ($status) {
        $status | ForEach-Object { Write-Output $_ }
    } else {
        Write-Output "(clean)"
    }
}

Show-Status

if ($Command -eq "check") {
    $branch = Get-CurrentBranch
    if ($branch -eq "main") {
        throw "Safety check refused on main. Use a dedicated feature branch."
    }

    Assert-NoTrackedBlockedPaths
    Assert-NoChangedBlockedPaths

    Write-Output ""
    Write-Output "git diff --check:"
    Invoke-Git -Args @("diff", "--check")
    Invoke-Git -Args @("diff", "--cached", "--check")
    Write-Output "diff check: ok"

    Write-Output ""
    Invoke-PythonCompile
}
