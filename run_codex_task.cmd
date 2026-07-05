@echo off
setlocal
cd /d "%~dp0"
set "CODEX_TASK_LAUNCHER_FILE=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$launcher=$env:CODEX_TASK_LAUNCHER_FILE; $text=Get-Content -Raw -Encoding UTF8 $launcher; $marker='# POWERSHELL_START'; $idx=$text.IndexOf($marker); if($idx -lt 0){Write-Host 'launcher marker not found' -ForegroundColor Red; exit 1}; $ps=$text.Substring($idx + $marker.Length); Invoke-Expression $ps"
exit /b %ERRORLEVEL%
# POWERSHELL_START
$ErrorActionPreference = 'Continue'

$Launcher = $env:CODEX_TASK_LAUNCHER_FILE
$Root = Split-Path -Parent $Launcher
Set-Location -Path $Root

$TaskDir = Join-Path $Root 'data\runtime\agent_tasks'
$LogDir = Join-Path $Root 'data\runtime\agent_logs'
New-Item -ItemType Directory -Force -Path $TaskDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$CurrentTask = Join-Path $TaskDir 'CURRENT_TASK.md'
$LastResult = Join-Path $TaskDir 'LAST_RESULT.md'
$TaskStamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$TaskSnapshot = Join-Path $TaskDir ("task_$TaskStamp.md")
$LogPath = Join-Path $LogDir ("codex_task_$TaskStamp.log")

function Write-Section($Text) {
    Write-Host ''
    Write-Host ('=== ' + $Text + ' ===') -ForegroundColor Cyan
}

function New-TaskTemplate {
@'
# Codex Task

Start time: when this task file is saved and the runner starts Codex.
Expected finish time: 5-15 minutes unless the task says otherwise.
Stop waiting time: stop if there is no progress for 20 minutes, or if Codex reports git lock, merge conflict, sandbox/helper failure, or unexpected dirty files.

## Goal
Write the task goal here.

## Scope
Allowed files or areas:
- 

Forbidden changes:
- Do not change live trading, OpenAPI, orders, or account behavior unless explicitly requested.
- Do not create unrelated documents.

## Validation
Run the relevant checks required by AGENTS.md and by the task.

## Commit / push
Commit and push only if this task explicitly asks for it.

## Report
Report changed files, checks run, results, commit hash, push status, and anything not verified.
'@
}

Write-Section 'Codex task runner'
Write-Host 'Repository:' $Root
Write-Host 'Task file :' $CurrentTask
Write-Host 'Log file  :' $LogPath
Write-Host ''

if (-not (Test-Path $CurrentTask)) {
    New-TaskTemplate | Set-Content -Path $CurrentTask -Encoding UTF8
    Write-Host 'Created task template. Edit it, save it, then close Notepad.' -ForegroundColor Yellow
    Start-Process notepad.exe -ArgumentList $CurrentTask -Wait
}

$TaskText = Get-Content -Path $CurrentTask -Raw -Encoding UTF8
if ([string]::IsNullOrWhiteSpace($TaskText) -or $TaskText -match 'Write the task goal here') {
    Write-Host 'Task file is empty or still contains the template placeholder.' -ForegroundColor Yellow
    Write-Host 'Edit it, save it, then close Notepad.' -ForegroundColor Yellow
    Start-Process notepad.exe -ArgumentList $CurrentTask -Wait
    $TaskText = Get-Content -Path $CurrentTask -Raw -Encoding UTF8
}

$TaskText | Set-Content -Path $TaskSnapshot -Encoding UTF8

$AgentHeader = @"
You are the Codex implementation agent working in the local aiTrade repository.

First read AGENTS.md at the repository root and follow it.
Do not ask the owner to copy/paste long instructions between tools.
Work inside the repository. Before editing, check git status --short.
If unexpected dirty files exist, stop and report.
Do not change live trading, OpenAPI, order, or account behavior unless the task explicitly requires it.
Use meaningful commits only. Do not commit tiny unrelated edits.
Run relevant validation checks. Do not claim checks that were not run.
When done, report changed files, validation results, commit hash, push status, and unverified items.

Task follows.
"@

$Prompt = $AgentHeader + "`r`n" + $TaskText
$Prompt | Set-Clipboard

$Codex = Get-Command codex -ErrorAction SilentlyContinue
if (-not $Codex) {
    Write-Host 'ERROR: codex command was not found.' -ForegroundColor Red
    Write-Host 'The prompt was copied to clipboard and saved here:' $TaskSnapshot
    @"
# Codex task runner result

Start: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
ExitCode: 1
Reason: codex command was not found.
Task: $TaskSnapshot
Log: $LogPath
"@ | Set-Content -Path $LastResult -Encoding UTF8
    Read-Host 'Press Enter to exit'
    exit 1
}

Write-Host 'codex CLI:' $Codex.Source
Write-Host 'Task snapshot:' $TaskSnapshot
Write-Host 'Prompt copied to clipboard as fallback.'

$StartTime = Get-Date
$ExitCode = 0

Write-Section 'Codex start'
try {
    $HelpText = (& codex --help 2>&1 | Out-String)
    if ($HelpText -match '(?m)\bexec\b') {
        Write-Host 'Mode: codex exec'
        & codex exec $Prompt 2>&1 | Tee-Object -FilePath $LogPath
    } else {
        Write-Host 'Mode: codex default'
        & codex $Prompt 2>&1 | Tee-Object -FilePath $LogPath
    }
    if ($null -ne $LASTEXITCODE) { $ExitCode = $LASTEXITCODE }
} catch {
    $ExitCode = 1
    $_ | Out-String | Tee-Object -FilePath $LogPath -Append
}

$FinishTime = Get-Date
$Elapsed = New-TimeSpan -Start $StartTime -End $FinishTime

Write-Section 'Local git status after Codex'
$GitStatus = (& git status --short 2>&1 | Out-String)
$GitHead = (& git log -1 --oneline 2>&1 | Out-String)
Write-Host $GitStatus
Write-Host $GitHead

$ResultText = @"
# Codex task runner result

Start: $($StartTime.ToString('yyyy-MM-dd HH:mm:ss'))
Finish: $($FinishTime.ToString('yyyy-MM-dd HH:mm:ss'))
Elapsed: $($Elapsed.ToString())
ExitCode: $ExitCode
Task: $TaskSnapshot
Log: $LogPath

## git status --short

```text
$GitStatus
```

## git log -1 --oneline

```text
$GitHead
```

## Notes

If Codex reports a Windows sandbox helper failure, fix the Codex installation or sandbox helper first. The task prompt is saved and also copied to the clipboard.
"@
$ResultText | Set-Content -Path $LastResult -Encoding UTF8

Write-Section 'Done'
Write-Host 'ExitCode:' $ExitCode
Write-Host 'Result file:' $LastResult
Write-Host 'Log file   :' $LogPath
Write-Host 'Task file  :' $CurrentTask
Write-Host ''
Write-Host 'Use Q/Ctrl+C only if Codex is stuck. For normal exit, press Enter after reading.' -ForegroundColor Gray
Read-Host 'Press Enter to exit'
exit $ExitCode
