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
; - Never send keys to the foreground window.
; - Send only to a verified Edit6 control HWND.
; - If the target control is missing or ambiguous, do nothing and write status.
; - Enter is sent only after ControlFocus + focus readback confirms Edit6.
; - After HTS link, restore the previously active window so browser arrow keys keep working.

TargetControl := "Edit6"
SendEnterAfterSet := true
NotifySuccess := false
LastClipboard := Clipboard
LastCommandId := ""
LastSentCode := ""
StatusFile := "C:\aiTrade\data\runtime\stockboard_v2\hts_link_status.txt"

SetTimer, WatchClipboardCommand, 80
WriteStatus("started", "", "bridge started")
TrayTip, StockBoard Kiwoom Link v2, HTS link bridge started, 1
return

WatchClipboardCommand:
    current := Clipboard
    if (current = LastClipboard)
        return
    LastClipboard := current

    if (!ParseStockCommand(current, commandId, code, parseMode))
        return

    if (commandId != "" && commandId = LastCommandId)
        return

    result := SendCodeToKiwoom(code, usedSpec, message)
    if (result) {
        if (commandId != "")
            LastCommandId := commandId
        LastSentCode := code
        WriteStatus("ok", code, "sent via " . usedSpec . " / " . parseMode . " / " . message)
    } else {
        WriteStatus("error", code, message)
    }
return

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
    global NotifySuccess
    global TargetControl

    WinGet, previousHwnd, ID, A

    target := FindSingleTargetControl(usedSpec, message)
    if (!IsObject(target)) {
        TrayTip, StockBoard Kiwoom Link v2, %message%, 3
        return false
    }

    controlHwnd := target.control
    windowHwnd := target.window

    Loop, 3 {
        ControlSetText,, %code%, ahk_id %controlHwnd%
        if (ErrorLevel) {
            message := "ControlSetText failed: " . usedSpec
            TrayTip, StockBoard Kiwoom Link v2, %message%, 3
            RestorePreviousWindow(previousHwnd, windowHwnd)
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
        RestorePreviousWindow(previousHwnd, windowHwnd)
        return false
    }

    if (SendEnterAfterSet) {
        ; Focus the verified Edit6 first, then verify focus by control name.
        ; If focus cannot be verified, never send Enter.
        ControlFocus, %TargetControl%, ahk_id %windowHwnd%
        Sleep, 45
        ControlGetFocus, focusedControl, ahk_id %windowHwnd%
        if (focusedControl != TargetControl) {
            message := "OK " . code . " / code set only, Edit6 focus not verified: " . focusedControl . " / " . usedSpec
            RestorePreviousWindow(previousHwnd, windowHwnd)
            return true
        }

        ControlSend, %TargetControl%, {Enter}, ahk_id %windowHwnd%
        if (ErrorLevel) {
            message := "OK " . code . " / code set, Enter send failed safely / " . usedSpec
            RestorePreviousWindow(previousHwnd, windowHwnd)
            return true
        }
        message := "OK " . code . " / enter sent after Edit6 focus verified / " . usedSpec
    } else {
        message := "OK " . code . " / code set only, enter blocked / " . usedSpec
    }

    RestorePreviousWindow(previousHwnd, windowHwnd)

    if (NotifySuccess) {
        TrayTip, StockBoard Kiwoom Link v2, %message%, 1
    }
    return true
}

RestorePreviousWindow(previousHwnd, targetWindowHwnd) {
    if (!previousHwnd)
        return
    if (previousHwnd = targetWindowHwnd)
        return
    Sleep, 60
    if WinExist("ahk_id " . previousHwnd) {
        WinActivate, ahk_id %previousHwnd%
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
        if (controlHwnd) {
            matches.Push({window: hwnd, control: controlHwnd})
        }
    }

    if (matches.Length() = 0) {
        usedSpec := ""
        message := "Target Edit6 not found. No keys sent. tried_hwnds=" . tried
        return 0
    }

    if (matches.Length() > 1) {
        usedSpec := ""
        message := "Target Edit6 ambiguous: " . matches.Length() . " controls. No keys sent."
        return 0
    }

    usedSpec := "hwnd " . matches[1].window . " / " . TargetControl . " " . matches[1].control
    return matches[1]
}

CandidateHtsWindows() {
    heroTitle := Chr(0xC601) . Chr(0xC6C5) . Chr(0xBB38)
    heroTitle4 := heroTitle . "4"
    specs := ["ahk_class _NKHeroMainClass", "ahk_class NHeroMainClass", heroTitle4, heroTitle]
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
