param()

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -Path $Root

$Branch = (git branch --show-current 2>$null | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($Branch)) { $Branch = 'hot-priority-integrated-20260630' }

$TaskFile = Join-Path $Root 'codex_tasks\CURRENT_TASK.md'
$TaskDir = Join-Path $Root 'data\runtime\agent_tasks'
$LogDir = Join-Path $Root 'data\runtime\agent_logs'
New-Item -ItemType Directory -Force -Path $TaskDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$Snapshot = Join-Path $TaskDir ("task_$Stamp.md")
$LogPath = Join-Path $LogDir ("codex_task_$Stamp.log")
$LastResult = Join-Path $TaskDir 'LAST_RESULT.md'

function Section($name) {
    Write-Host ''
    Write-Host "=== $name ===" -ForegroundColor Cyan
}

function SaveResult($code, $reason) {
    $status = git status --short 2>&1 | Out-String
    $head = git log -1 --oneline 2>&1 | Out-String
    @"
# Codex task runner result

Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
ExitCode: $code
Reason: $reason
Task: $Snapshot
Log: $LogPath

## git status --short

```text
$status
```

## git log -1 --oneline

```text
$head
```
"@ | Set-Content -Path $LastResult -Encoding UTF8
}

Section 'Codex task runner'
Write-Host "Repository: $Root"
Write-Host "Branch    : $Branch"
Write-Host "Task file : $TaskFile"
Write-Host "Log file  : $LogPath"

Section 'Preflight'
$dirty = (git status --short 2>&1 | Out-String).Trim()
if ($dirty) {
    Write-Host 'Working tree is not clean. Stop before running Codex.' -ForegroundColor Red
    Write-Host $dirty
    SaveResult 2 'working tree is not clean'
    Read-Host 'Press Enter to exit'
    exit 2
}

Write-Host 'Pull latest task and runner from origin...'
git pull --ff-only origin $Branch
if ($LASTEXITCODE -ne 0) {
    SaveResult 3 'git pull failed'
    Read-Host 'Press Enter to exit'
    exit 3
}

if (-not (Test-Path $TaskFile)) {
    Write-Host 'Task file not found. Ask ChatGPT to update codex_tasks/CURRENT_TASK.md.' -ForegroundColor Red
    SaveResult 4 'task file not found'
    Read-Host 'Press Enter to exit'
    exit 4
}

$task = Get-Content -Path $TaskFile -Raw -Encoding UTF8
if ([string]::IsNullOrWhiteSpace($task) -or $task -match 'Status:\s*idle') {
    Write-Host 'No active task in codex_tasks/CURRENT_TASK.md.' -ForegroundColor Yellow
    SaveResult 0 'no active task'
    Read-Host 'Press Enter to exit'
    exit 0
}
$task | Set-Content -Path $Snapshot -Encoding UTF8

$prefix = @'
You are the Codex implementation agent working in the local aiTrade repository.
First read AGENTS.md at the repository root and follow it.
Do not ask the owner to copy/paste long instructions between ChatGPT, Codex, and GitHub.
Before editing, check git status --short. If unexpected dirty files exist, stop and report.
Do not change live trading, OpenAPI, order, or account behavior unless the task explicitly requires it.
Run relevant validation checks and report changed files, validation results, commit hash, push status, and unverified items.

Task follows.
'@
$prompt = $prefix + "`r`n" + $task

$codex = Get-Command codex -ErrorAction SilentlyContinue
if (-not $codex) {
    Write-Host 'ERROR: codex command was not found.' -ForegroundColor Red
    SaveResult 1 'codex command was not found'
    Read-Host 'Press Enter to exit'
    exit 1
}

Write-Host "codex CLI: $($codex.Source)"
Write-Host "Task snapshot: $Snapshot"

$start = Get-Date
$code = 0
Section 'Codex start'
try {
    $help = codex --help 2>&1 | Out-String
    if ($help -match '(?m)\bexec\b') {
        codex exec $prompt 2>&1 | Tee-Object -FilePath $LogPath
    } else {
        codex $prompt 2>&1 | Tee-Object -FilePath $LogPath
    }
    if ($null -ne $LASTEXITCODE) { $code = $LASTEXITCODE }
} catch {
    $code = 1
    $_ | Out-String | Tee-Object -FilePath $LogPath -Append
}

$finish = Get-Date
$status = git status --short 2>&1 | Out-String
$head = git log -1 --oneline 2>&1 | Out-String

Section 'Local git status after Codex'
Write-Host $status
Write-Host $head

@"
# Codex task runner result

Start: $($start.ToString('yyyy-MM-dd HH:mm:ss'))
Finish: $($finish.ToString('yyyy-MM-dd HH:mm:ss'))
ExitCode: $code
Task: $Snapshot
Log: $LogPath

## git status --short

```text
$status
```

## git log -1 --oneline

```text
$head
```
"@ | Set-Content -Path $LastResult -Encoding UTF8

Section 'Done'
Write-Host "ExitCode: $code"
Write-Host "Result file: $LastResult"
Write-Host "Log file   : $LogPath"
Read-Host 'Press Enter to exit'
exit $code
