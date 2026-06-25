#NoEnv
#SingleInstance Force
#Persistent
SendMode Input
SetTitleMatchMode, 2

; StockBoard -> clipboard -> Kiwoom HTS bridge for AutoHotkey v1.
; Valid clipboard values: 005930, 005930_AL, 005930_NX.
; HTS Edit6 always receives the final 6-digit stock code only.

TargetControl := "Edit6"
AllowDuplicate := false
SendEnterAfterSet := true
UseWinActivateFallback := false
NotifySuccess := false
LastCode := ""
LastClipboard := Clipboard

SetTimer, WatchClipboard, 250
TrayTip, StockBoard Kiwoom Link v1, StockBoard link started, 1
return

WatchClipboard:
    if (Clipboard = LastClipboard)
        return

    LastClipboard := Clipboard
    rawCode := Trim(Clipboard)
    code := NormalizeClipboardStockCode(rawCode)
    if (code = "")
        return

    if (!AllowDuplicate && code = LastCode)
        return

    result := SendCodeToKiwoom(code, usedSpec)
    if (result) {
        LastCode := code
    }
return

NormalizeClipboardStockCode(rawText) {
    text := Trim(rawText)
    if RegExMatch(text, "^\d{6}$")
        return text
    if RegExMatch(text, "^(\d{6})_(AL|NX)$", match)
        return match1
    return ""
}

SendCodeToKiwoom(code, ByRef usedSpec) {
    global TargetControl
    global SendEnterAfterSet
    global UseWinActivateFallback
    global NotifySuccess

    hwnd := FindTargetWindow(usedSpec, triedSpecs)
    if (!hwnd) {
        TrayTip, StockBoard Kiwoom Link v1, HTS window not found: %triedSpecs%, 3
        return false
    }

    ControlSetText, %TargetControl%, %code%, ahk_id %hwnd%
    if (ErrorLevel) {
        if (!UseWinActivateFallback) {
            TrayTip, StockBoard Kiwoom Link v1, ControlSetText failed: %usedSpec% / %TargetControl%, 3
            return false
        }

        WinActivate, ahk_id %hwnd%
        WinWaitActive, ahk_id %hwnd%,, 1
        if (ErrorLevel) {
            TrayTip, StockBoard Kiwoom Link v1, WinActivate fallback failed: %usedSpec%, 3
            return false
        }

        ControlFocus, %TargetControl%, ahk_id %hwnd%
        ControlSetText, %TargetControl%, %code%, ahk_id %hwnd%
        if (ErrorLevel) {
            TrayTip, StockBoard Kiwoom Link v1, fallback ControlSetText failed: %usedSpec%, 3
            return false
        }
    }

    ControlGetText, readback, %TargetControl%, ahk_id %hwnd%
    if (readback != code) {
        TrayTip, StockBoard Kiwoom Link v1, Edit6 set failed. expected %code%, got %readback%, 3
        return false
    }

    if (SendEnterAfterSet) {
        ControlSend, %TargetControl%, {Enter}, ahk_id %hwnd%
        if (ErrorLevel) {
            TrayTip, StockBoard Kiwoom Link v1, Enter send failed: %usedSpec% / %TargetControl%, 3
            return false
        }
        if (NotifySuccess) {
            TrayTip, StockBoard Kiwoom Link v1, Edit6 set OK: %code% / %usedSpec%. Enter sent to Edit6., 1
        }
    } else {
        if (NotifySuccess) {
            TrayTip, StockBoard Kiwoom Link v1, Edit6 set OK: %code% / %usedSpec%, 1
        }
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
