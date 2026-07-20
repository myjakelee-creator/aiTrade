#NoEnv
#SingleInstance Force
#Persistent
SendMode Input
SetTitleMatchMode, 2
DetectHiddenWindows, Off
SetBatchLines, -1

; StockBoard v2 -> clipboard command -> Kiwoom HTS bridge for AutoHotkey v1.
; Accepted clipboard commands:
;   SBV2|<sequence>|005930
;   SB|<sequence>|005930
; Fallback accepted values:
;   005930
;   005930_AL
;   005930_NX
;
; Safety policy:
; - Never activate HTS.
; - Never focus HTS/Edit6.
; - Never send keys to the foreground window.
; - Write the code only to one verified Edit6 HWND.
; - If multiple Edit6 controls exist, select only a unique highest-scoring visible HTS main window.
; - Enter is posted only to that Edit6 HWND.
; - After a verified HTS linkage, replace the StockBoard command in Clipboard with the plain 6-digit code.
; - Suppress the bridge's own Clipboard write so the raw-code fallback cannot send the same code twice.
; - After HTS linkage, reactivate the previous browser/page window so ArrowUp/Down keeps working.
; - Clipboard can be busy while Chrome/Windows owns it; retry briefly and skip the tick instead of crashing.
; - Do not compare launcher PID with the elevated AHK PID. RunAs can report a different PID.
; - Exit voluntarily only when an explicit stop flag exists.

TargetControl := "Edit6"
SendEnterAfterSet := true
NotifySuccess := false
StoreSentCodeInClipboard := true
LastClipboard := ""
SafeReadClipboard(LastClipboard)
LastCommandId := ""
LastSentCode := ""
LastBridgeClipboardCode := ""
SuppressBridgeClipboardUntil := 0
StatusFile := "C:\aiTrade\data\runtime\stockboard_v2\hts_link_status.txt"
PidFile := "C:\aiTrade\data\runtime\stockboard_v2\stockboard_v2_ahk.pid"
StopFile := "C:\aiTrade\data\runtime\stockboard_v2\stockboard_v2_ahk.stop"
SelfPid := DllCall("GetCurrentProcessId")

FileCreateDir, C:\aiTrade\data\runtime\stockboard_v2
FileDelete, %StopFile%
FileDelete, %PidFile%
FileAppend, %SelfPid%, %PidFile%, UTF-8

SetTimer, WatchClipboardCommand, 80
SetTimer, WatchStopFlag, 500
WriteStatus("started", "", "bridge started pid " . SelfPid)
TrayTip, StockBoard Kiwoom Link v2, HTS link bridge started, 1
return

WatchStopFlag:
    if (FileExist(StopFile)) {
        WriteStatus("stopping", "", "explicit stop flag detected; exiting bridge")
        FileDelete, %StopFile%
        FileDelete, %PidFile%
        ExitApp
    }
return

WatchClipboardCommand:
    if (!SafeReadClipboard(current))
        return
    if (current = LastClipboard)
        return
    LastClipboard := current

    if (!ParseStockCommand(current, commandId, code, parseMode))
        return

    if (parseMode = "raw_code"
        && code = LastBridgeClipboardCode
        && A_TickCount <= SuppressBridgeClipboardUntil)
        return

    if (commandId != "" && commandId = LastCommandId)
        return

    result := SendCodeToKiwoom(code, usedSpec, message)
    if (result) {
        if (commandId != "")
            LastCommandId := commandId
        LastSentCode := code

        clipboardNote := "clipboard unchanged"
        if (StoreSentCodeInClipboard) {
            LastBridgeClipboardCode := code
            SuppressBridgeClipboardUntil := A_TickCount + 2000
            if (SafeWriteClipboard(code)) {
                LastClipboard := code
                clipboardNote := "clipboard saved " . code
            } else {
                LastBridgeClipboardCode := ""
                SuppressBridgeClipboardUntil := 0
                clipboardNote := "clipboard save failed"
            }
        }

        WriteStatus("ok", code, "sent via " . usedSpec . " / " . parseMode . " / " . message . " / " . clipboardNote)
    } else {
        WriteStatus("error", code, message)
    }
return

SafeReadClipboard(ByRef text) {
    text := ""
    Loop, 5 {
        try {
            text := Clipboard
            return true
        } catch e {
            Sleep, 30
        }
    }
    return false
}

SafeWriteClipboard(text) {
    Loop, 5 {
        try {
            Clipboard := text
            ClipWait, 0.3
            if (!ErrorLevel) {
                verify := Clipboard
                if (verify = text)
                    return true
            }
        } catch e {
        }
        Sleep, 30
    }
    return false
}

ParseStockCommand(rawText, ByRef commandId, ByRef code, ByRef parseMode) {
    text := Trim(rawText)
    commandId := ""
    code := ""
    parseMode := ""

    if RegExMatch(text, "i)^SBV?2?\|(\d+)\|(\d{6})(?:_(?:AL|NX))?$", match) {
        commandId := match1
        code := match2
        parseMode := "stockboard_command"
        return true
    }

    if RegExMatch(text, "^\d{6}$") {
        commandId := "raw-" . A_TickCount
        code := text
        parseMode := "raw_code"
        return true
    }

    if RegExMatch(text, "^(\d{6})_(AL|NX)$", match) {
        commandId := "raw-" . A_TickCount
        code := match1
        parseMode := "raw_suffix_code"
        return true
    }

    return false
}

SendCodeToKiwoom(code, ByRef usedSpec, ByRef message) {
    global SendEnterAfterSet

    WinGet, previousHwnd, ID, A

    target := FindSingleTargetControl(usedSpec, message)
    if (!IsObject(target)) {
        TrayTip, StockBoard Kiwoom Link v2, %message%, 3
        RestorePreviousWindow(previousHwnd)
        return false
    }

    controlHwnd := target.control

    Loop, 3 {
        ControlSetText,, %code%, ahk_id %controlHwnd%
        if (ErrorLevel) {
            message := "ControlSetText failed: " . usedSpec
            TrayTip, StockBoard Kiwoom Link v2, %message%, 3
            RestorePreviousWindow(previousHwnd)
            return false
        }
        Sleep, 35
        ControlGetText, readback,, ahk_id %controlHwnd%
        if (readback = code)
            break
        Sleep, 60
    }

    ControlGetText, readback,, ahk_id %controlHwnd%
    if (readback != code) {
        message := "Edit6 set failed. expected " . code . ", got " . readback
        TrayTip, StockBoard Kiwoom Link v2, %message%, 3
        RestorePreviousWindow(previousHwnd)
        return false
    }

    if (SendEnterAfterSet) {
        if (PostEnterToEdit(controlHwnd)) {
            message := "OK " . code . " / enter posted to Edit6 HWND without HTS focus / " . usedSpec
        } else {
            message := "OK " . code . " / code set only, Enter post failed safely / " . usedSpec
        }
    } else {
        message := "OK " . code . " / code set only, enter blocked / " . usedSpec
    }

    RestorePreviousWindow(previousHwnd)

    if (NotifySuccess) {
        TrayTip, StockBoard Kiwoom Link v2, %message%, 1
    }
    return true
}

PostEnterToEdit(controlHwnd) {
    if (!controlHwnd)
        return false

    PostMessage, 0x100, 0x0D, 0x001C0001,, ahk_id %controlHwnd%
    Sleep, 20
    PostMessage, 0x101, 0x0D, 0xC01C0001,, ahk_id %controlHwnd%
    return true
}

RestorePreviousWindow(previousHwnd) {
    if (!previousHwnd)
        return

    Loop, 6 {
        Sleep, 70
        if !WinExist("ahk_id " . previousHwnd)
            return

        WinActivate, ahk_id %previousHwnd%
        WinWaitActive, ahk_id %previousHwnd%,, 0.35

        ControlGet, chromeRenderer, Hwnd,, Chrome_RenderWidgetHostHWND1, ahk_id %previousHwnd%
        if (chromeRenderer) {
            ControlFocus,, ahk_id %chromeRenderer%
            continue
        }

        ControlGet, ieRenderer, Hwnd,, Internet Explorer_Server1, ahk_id %previousHwnd%
        if (ieRenderer) {
            ControlFocus,, ahk_id %ieRenderer%
            continue
        }
    }
}

FindSingleTargetControl(ByRef usedSpec, ByRef message) {
    global TargetControl
    windows := CandidateHtsWindows()
    matches := []
    tried := ""

    for index, hwnd in windows {
        if (tried != "")
            tried .= ", "
        tried .= hwnd
        if (!hwnd)
            continue

        ControlGet, controlHwnd, Hwnd,, %TargetControl%, ahk_id %hwnd%
        if (!controlHwnd)
            continue

        score := ScoreTargetControl(hwnd, controlHwnd, detail)
        matches.Push({window: hwnd, control: controlHwnd, score: score, detail: detail})
    }

    if (matches.Length() = 0) {
        usedSpec := ""
        message := "Target Edit6 not found. No keys sent. tried_hwnds=" . tried
        return 0
    }

    bestScore := -999999
    bestIndex := 0
    bestCount := 0
    summaries := ""
    for index, item in matches {
        if (summaries != "")
            summaries .= " | "
        summaries .= item.window . ":" . item.score . ":" . item.detail
        if (item.score > bestScore) {
            bestScore := item.score
            bestIndex := index
            bestCount := 1
        } else if (item.score = bestScore) {
            bestCount += 1
        }
    }

    if (bestIndex = 0 || bestCount != 1) {
        usedSpec := ""
        message := "Target Edit6 ambiguous after scoring. No keys sent. candidates=" . summaries
        return 0
    }

    selected := matches[bestIndex]
    usedSpec := "hwnd " . selected.window . " / " . TargetControl . " " . selected.control . " / score " . selected.score
    message := "selected unique highest score / " . selected.detail
    return selected
}

ScoreTargetControl(windowHwnd, controlHwnd, ByRef detail) {
    score := 0
    WinGetClass, className, ahk_id %windowHwnd%
    WinGet, processName, ProcessName, ahk_id %windowHwnd%
    WinGetTitle, titleText, ahk_id %windowHwnd%
    WinGet, minMax, MinMax, ahk_id %windowHwnd%
    WinGetPos, winX, winY, winW, winH, ahk_id %windowHwnd%
    ControlGet, isVisible, Visible,,, ahk_id %controlHwnd%
    ControlGet, isEnabled, Enabled,,, ahk_id %controlHwnd%

    if (className = "_NKHeroMainClass")
        score += 1200
    else if (className = "NHeroMainClass")
        score += 1100

    if (processName = "nkre.exe")
        score += 600
    if InStr(titleText, Chr(0xC601) . Chr(0xC6C5) . Chr(0xBB38))
        score += 350
    if (isVisible)
        score += 180
    if (isEnabled)
        score += 120
    if (minMax != -1)
        score += 60

    areaScore := Floor((winW * winH) / 20000)
    if (areaScore > 250)
        areaScore := 250
    if (areaScore > 0)
        score += areaScore

    detail := "class=" . className . ",exe=" . processName . ",title=" . titleText . ",visible=" . isVisible . ",enabled=" . isEnabled . ",minmax=" . minMax . ",size=" . winW . "x" . winH
    return score
}

CandidateHtsWindows() {
    heroTitle := Chr(0xC601) . Chr(0xC6C5) . Chr(0xBB38)
    heroTitle4 := heroTitle . "4"
    specs := ["ahk_class _NKHeroMainClass", "ahk_class NHeroMainClass", "ahk_exe nkre.exe", heroTitle4, heroTitle]
    result := []
    seen := {}

    for index, spec in specs {
        WinGet, list, List, %spec%
        Loop, %list% {
            hwnd := list%A_Index%
            if (hwnd && !seen.HasKey(hwnd)) {
                seen[hwnd] := true
                result.Push(hwnd)
            }
        }
    }

    return result
}

WriteStatus(status, code, message) {
    global StatusFile
    FormatTime, nowText,, yyyy-MM-dd HH:mm:ss
    line := nowText . "|" . status . "|" . code . "|" . message
    FileCreateDir, C:\aiTrade\data\runtime\stockboard_v2
    FileDelete, %StatusFile%
    FileAppend, %line%, %StatusFile%, UTF-8
}
