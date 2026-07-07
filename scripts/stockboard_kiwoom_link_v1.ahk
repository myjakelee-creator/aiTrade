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
; The bridge no longer ignores duplicate stock codes. Every new command sequence
; is processed once, so clicking the same row repeatedly works. The bridge does
; not activate or close HTS windows. It only writes the verified code to Edit6
; and sends Enter to that exact control after readback succeeds.

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
        WriteStatus("ok", code, "sent via " . usedSpec . " / " . parseMode)
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
    global TargetControl
    global SendEnterAfterSet
    global NotifySuccess

    hwnd := FindTargetWindow(usedSpec, triedSpecs)
    if (!hwnd) {
        message := "HTS window not found: " . triedSpecs
        TrayTip, StockBoard Kiwoom Link v2, %message%, 3
        return false
    }

    ControlGet, controlHwnd, Hwnd,, %TargetControl%, ahk_id %hwnd%
    if (!controlHwnd) {
        message := "Target control not found: " . TargetControl . " / " . usedSpec
        TrayTip, StockBoard Kiwoom Link v2, %message%, 3
        return false
    }

    Loop, 3 {
        ControlSetText,, %code%, ahk_id %controlHwnd%
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
        return false
    }

    if (SendEnterAfterSet) {
        ; Send Enter to the Edit6 control HWND directly. Do not activate HTS and
        ; do not send keys to the foreground window.
        ControlSend,, {Enter}, ahk_id %controlHwnd%
        if (ErrorLevel) {
            message := "Enter send failed: " . usedSpec . " / " . TargetControl
            TrayTip, StockBoard Kiwoom Link v2, %message%, 3
            return false
        }
    }

    message := "OK " . code . " / " . usedSpec
    if (NotifySuccess) {
        TrayTip, StockBoard Kiwoom Link v2, %message%, 1
    }
    return true
}

FindTargetWindow(ByRef usedSpec, ByRef triedSpecs) {
    heroTitle := Chr(0xC601) . Chr(0xC6C5) . Chr(0xBB38)
    heroTitle4 := heroTitle . "4"
    specs := ["ahk_class _NKHeroMainClass", "ahk_class NHeroMainClass", heroTitle4, heroTitle]
    triedSpecs := ""

    for index, spec in specs {
        if (triedSpecs != "")
            triedSpecs .= ", "
        triedSpecs .= spec

        WinGet, hwnd, ID, %spec%
        if (hwnd) {
            usedSpec := spec
            return hwnd
        }
    }

    usedSpec := ""
    return 0
}

WriteStatus(status, code, message) {
    global StatusFile
    FormatTime, nowText,, yyyy-MM-dd HH:mm:ss
    line := nowText . "|" . status . "|" . code . "|" . message
    FileCreateDir, C:\aiTrade\data\runtime\stockboard_v2
    FileDelete, %StatusFile%
    FileAppend, %line%, %StatusFile%, UTF-8
}
