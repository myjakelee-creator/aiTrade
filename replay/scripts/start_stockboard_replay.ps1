param(
    [int]$Port = 18010,
    [string]$StartTime = "08:59:00",
    [switch]$NoOpenBoard
)

$ErrorActionPreference = "Continue"

$ReplayRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Root = Split-Path -Parent $ReplayRoot
Set-Location -Path $Root

$Base = "http://127.0.0.1:$Port"
$RuntimeDir = Join-Path $Root "data\runtime"
$OutDir = Join-Path $Root "data\runtime\replay_output"
$InboxZip = Join-Path $Root "data\runtime\replay_inbox\live_raw_20260616.zip"
$ServerPath = Join-Path $ReplayRoot "stockboard_live_compatible_replay_server.py"
$BuilderPath = Join-Path $ReplayRoot "tools\replay_tick_event_builder.py"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Api {
    param([string]$Path)
    try {
        Invoke-RestMethod "$Base$Path" -TimeoutSec 3
    } catch {
        return $null
    }
}

function TimeToMs {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) { return 0 }

    $t = $Text.Trim()
    if ($t.Contains("T")) { $t = $t.Split("T")[-1] }

    $parts = $t.Split(":")
    if ($parts.Count -lt 3) { return 0 }

    $h = [int]$parts[0]
    $m = [int]$parts[1]
    $secFloat = [double]$parts[2]
    $s = [math]::Floor($secFloat)
    $ms = [math]::Round(($secFloat - $s) * 1000)

    return (($h * 3600000) + ($m * 60000) + ($s * 1000) + $ms)
}

function MsToTime {
    param([double]$Ms)

    $day = 86400000
    $v = [int][math]::Round($Ms % $day)
    if ($v -lt 0) { $v += $day }

    $h = [math]::Floor($v / 3600000)
    $v -= $h * 3600000

    $m = [math]::Floor($v / 60000)
    $v -= $m * 60000

    $s = [math]::Floor($v / 1000)
    $v -= $s * 1000

    return ("{0:D2}:{1:D2}:{2:D2}.{3:D3}" -f [int]$h, [int]$m, [int]$s, [int]$v)
}

function CurrentTargetTime {
    $s = Api "/api/replay_driver/status"
    if ($s -and $s.target_time) { return [string]$s.target_time }
    if ($s -and $s.last_applied_tick) { return [string]$s.last_applied_tick }
    return "08:59:00.000"
}

function GotoTime {
    param([string]$TimeText)
    $encoded = [uri]::EscapeDataString($TimeText)
    Api "/api/replay_driver/goto?time=$encoded" | Out-Null
}

function JumpMs {
    param([int]$DeltaMs)
    $current = CurrentTargetTime
    $target = MsToTime ((TimeToMs $current) + $DeltaMs)
    GotoTime $target
}

function PlaySpeed {
    param([double]$Speed)
    Api ("/api/replay_driver/play?speed=" + $Speed) | Out-Null
}

function PauseReplay {
    Api "/api/replay_driver/pause" | Out-Null
}

function ResetReplay {
    Api "/api/replay_driver/reset" | Out-Null
}

function StopReplayAll {
    try { PauseReplay } catch {}
    try { StopPort -PortNumber $Port } catch {}
    throw "QUIT"
}

function StopPort {
    param([int]$PortNumber)
    try {
        $listeners = @(Get-NetTCPConnection -LocalPort $PortNumber -State Listen -ErrorAction SilentlyContinue)
        foreach ($listener in $listeners) {
            $pidToStop = $listener.OwningProcess
            if ($pidToStop -and $pidToStop -ne $PID) {
                Stop-Process -Id $pidToStop -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 300
            }
        }
    } catch {
    }
}

function EnsureReplayEvents {
    $events = Get-ChildItem $OutDir -Filter "tick_replay_*_20260616_085900_20260616_091000_events.jsonl" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if ($events) { return }

    Write-Host "Replay events not found. Building 08:59:00~09:10:00..." -ForegroundColor Yellow

    if (-not (Test-Path $BuilderPath)) { throw "Missing builder: $BuilderPath" }
    if (-not (Test-Path $InboxZip)) { throw "Missing replay zip: $InboxZip" }

    & py -3.10-32 $BuilderPath `
        --zip $InboxZip `
        --start "2026-06-16T08:59:00" `
        --end "2026-06-16T09:10:00" `
        --out-dir $OutDir `
        --top-n 100 `
        --progress 50000

    if ($LASTEXITCODE -ne 0) { throw "Replay event build failed." }
}

function StartReplayServer {
    if (-not (Test-Path $ServerPath)) { throw "Missing server: $ServerPath" }

    StopPort -PortNumber $Port

    $stdout = Join-Path $RuntimeDir ("start_stockboard_replay_server_stdout_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
    $stderr = Join-Path $RuntimeDir ("start_stockboard_replay_server_stderr_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

    $proc = Start-Process `
        -FilePath "py" `
        -ArgumentList @(
            "-3.10-32",
            $ServerPath,
            "--host", "127.0.0.1",
            "--port", "$Port",
            "--out-dir", $OutDir,
            "--start-paused"
        ) `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr

    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 200
        $s = Api "/api/replay_driver/status"
        if ($s) { return $proc }
    }

    Write-Host "Server stdout: $stdout" -ForegroundColor Yellow
    Write-Host "Server stderr: $stderr" -ForegroundColor Yellow
    throw "Replay server did not start."
}

function DrawStatus {
    param($s)

    Clear-Host
    Write-Host ""
    Write-Host "====================== StockBoard Replay Start ======================" -ForegroundColor Cyan
    Write-Host ""

    if (-not $s) {
        Write-Host "SERVER NOT RESPONDING" -ForegroundColor Red
        Write-Host "Base URL: $Base" -ForegroundColor Yellow
        return
    }

    $lag = [double]$s.processing_lag_ms
    $lagColor = "Green"
    if ($lag -ge 1000) { $lagColor = "Red" }
    elseif ($lag -ge 200) { $lagColor = "Yellow" }

    Write-Host ("Original Tick : {0}" -f $s.last_applied_tick) -ForegroundColor White
    Write-Host ("Target Time   : {0}" -f $s.target_time) -ForegroundColor Yellow
    Write-Host ("Process Lag   : {0} ms" -f $s.processing_lag_ms) -ForegroundColor $lagColor
    Write-Host ""
    Write-Host ("Event Gap     : {0} ms" -f $s.event_gap_ms) -ForegroundColor DarkYellow
    Write-Host ("Next Tick     : {0}" -f $s.next_tick) -ForegroundColor Gray
    Write-Host ("Events        : {0:N0} / {1:N0}" -f [double]$s.processed_events, [double]$s.total_events) -ForegroundColor Cyan
    Write-Host ""
    Write-Host ("api_top100_ms : {0} ms" -f $s.api_top100_ms) -ForegroundColor Gray
    Write-Host ("api_patch_ms  : {0} ms" -f $s.api_patch_ms) -ForegroundColor Gray
    Write-Host ("play/speed    : {0} / {1}x" -f $s.playing, $s.speed) -ForegroundColor Gray
    Write-Host ("board         : {0}/" -f $Base) -ForegroundColor Gray
    Write-Host ""
    Write-Host "----------------------------- Controls ----------------------------" -ForegroundColor DarkCyan
    Write-Host "Space: play/pause | R: 08:59:00 | 9: 09:00:00 | H: reset" -ForegroundColor White
    Write-Host "Left/Right: -10s/+10s | Up/Down: +1m/-1m | PgUp/PgDn: -5m/+5m" -ForegroundColor White
    Write-Host "1: 1x | 5: 5x | 0: 10x | 3: 30x | B: open board | Q/X: stop all" -ForegroundColor White
    Write-Host "-------------------------------------------------------------------" -ForegroundColor DarkCyan
}

function OpenBoard {
    Start-Process "$Base/"
}

function HandleKey {
    param($Key, $LastStatus)

    switch ($Key.Key) {
        "Spacebar" {
            if ($LastStatus -and $LastStatus.playing) { PauseReplay } else { PlaySpeed 1 }
            return
        }
        "LeftArrow" { JumpMs -10000; return }
        "RightArrow" { JumpMs 10000; return }
        "UpArrow" { JumpMs 60000; return }
        "DownArrow" { JumpMs -60000; return }
        "PageUp" { JumpMs -300000; return }
        "PageDown" { JumpMs 300000; return }
    }

    switch ($Key.KeyChar) {
        'q' { StopReplayAll; return }
        'Q' { StopReplayAll; return }
        'x' { StopReplayAll; return }
        'X' { StopReplayAll; return }
        'r' { GotoTime "08:59:00"; return }
        'R' { GotoTime "08:59:00"; return }
        'h' { ResetReplay; return }
        'H' { ResetReplay; return }
        '9' { GotoTime "09:00:00"; return }
        '1' { PlaySpeed 1; return }
        '5' { PlaySpeed 5; return }
        '0' { PlaySpeed 10; return }
        '3' { PlaySpeed 30; return }
        'b' { OpenBoard; return }
        'B' { OpenBoard; return }
    }
}

$startedProc = $null

try {
    EnsureReplayEvents
    $startedProc = StartReplayServer

    GotoTime $StartTime

    if (-not $NoOpenBoard) {
        OpenBoard
    }

    while ($true) {
        $s = Api "/api/replay_driver/status"
        DrawStatus $s

        $until = (Get-Date).AddMilliseconds(200)
        while ((Get-Date) -lt $until) {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                HandleKey -Key $key -LastStatus $s
                break
            }
            Start-Sleep -Milliseconds 25
        }
    }
}
catch {
    if ($_.Exception.Message -ne "QUIT") {
        Write-Host ""
        Write-Host $_ -ForegroundColor Red
        Read-Host "Press Enter to exit"
    }
}
finally {
    if ($startedProc -and -not $startedProc.HasExited) {
        Stop-Process -Id $startedProc.Id -Force -ErrorAction SilentlyContinue
    }
}