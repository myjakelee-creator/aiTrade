@echo off
setlocal

set "AHK_EXE=C:\Program Files\AutoHotkey\AutoHotkey.exe"
set "SCRIPT_DIR=%~dp0"
set "BRIDGE_SCRIPT=%SCRIPT_DIR%stockboard_kiwoom_link_v1.ahk"

if not exist "%AHK_EXE%" (
  echo AutoHotkey v1 executable was not found:
  echo   %AHK_EXE%
  echo.
  pause
  exit /b 1
)

if not exist "%BRIDGE_SCRIPT%" (
  echo StockBoard Kiwoom v1 bridge script was not found:
  echo   %BRIDGE_SCRIPT%
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%AHK_EXE%' -ArgumentList '\"%BRIDGE_SCRIPT%\"' -Verb RunAs"
if errorlevel 1 (
  echo Failed to request administrator launch.
  echo.
  pause
  exit /b 1
)

endlocal
