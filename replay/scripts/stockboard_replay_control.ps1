param(
    [string]$Base = "http://127.0.0.1:18010",
    [int]$RefreshMs = 200,
    [switch]$Once
)

$ErrorActionPreference = "Continue"

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
    if ($t.Contains("T")) {
        $t = $t.Split("T")[-1]
    }

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
    Api ("/api/replay_driver/goto?time=" + [uri]::EscapeDataString($TimeText)) | Out-Null
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

function DrawStatus {
    param($s)

    Clear-Host
    Write-Host ""
    Write-Host "====================== StockBoard Replay Control ======================" -ForegroundColor Cyan
    Write-Host ""

    if (-not $s) {
        Write-Host "SERVER NOT RESPONDING" -ForegroundColor Red
        Write-Host ""
        Write-Host "Base URL : $Base" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Start server first:" -ForegroundColor Gray
        Write-Host "py -3.10-32 stockboard_live_compatible_replay_server.py --port 18010 --start-paused"
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
    Write-Host ""
    Write-Host "----------------------------- Controls ------------------------------" -ForegroundColor DarkCyan
    Write-Host "Space: play/pause | R: 08:59:00 | 9: 09:00:00 | H: reset" -ForegroundColor White
    Write-Host "Left/Right: -10s/+10s | Up/Down: +1m/-1m | PgUp/PgDn: -5m/+5m" -ForegroundColor White
    Write-Host "1: 1x | 5: 5x | 0: 10x | 3: 30x | S: refresh | Q: quit" -ForegroundColor White
    Write-Host "---------------------------------------------------------------------" -ForegroundColor DarkCyan
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
        'q' { throw "QUIT" }
        'Q' { throw "QUIT" }
        'r' { GotoTime "08:59:00"; return }
        'R' { GotoTime "08:59:00"; return }
        'h' { ResetReplay; return }
        'H' { ResetReplay; return }
        '9' { GotoTime "09:00:00"; return }
        '1' { PlaySpeed 1; return }
        '5' { PlaySpeed 5; return }
        '0' { PlaySpeed 10; return }
        '3' { PlaySpeed 30; return }
        's' { return }
        'S' { return }
    }
}

$lastStatus = Api "/api/replay_driver/status"
DrawStatus $lastStatus

if ($Once) {
    exit 0
}

try {
    while ($true) {
        $lastStatus = Api "/api/replay_driver/status"
        DrawStatus $lastStatus

        $until = (Get-Date).AddMilliseconds($RefreshMs)
        while ((Get-Date) -lt $until) {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                HandleKey -Key $key -LastStatus $lastStatus
                break
            }
            Start-Sleep -Milliseconds 25
        }
    }
} catch {
    if ($_.Exception.Message -ne "QUIT") {
        Write-Host $_ -ForegroundColor Red
        Read-Host "Press Enter to exit"
    }
}