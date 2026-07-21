@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d C:\aiTrade

set "ACTION=%~1"
set "PAUSE_AT_END=%~2"
if "%ACTION%"=="" (
  set "PAUSE_AT_END=1"
  goto menu
)
if "%PAUSE_AT_END%"=="" set "PAUSE_AT_END=0"
goto run

:menu
echo.
echo StockBoard v2 Public Read-Only Gateway
echo.
echo   1 Start/restart fresh current UI gateway on port 8767
echo   2 Show status
echo   3 Publish fresh current UI with Tailscale Funnel
echo   4 Unpublish and restore private Tailscale Serve
echo   5 Stop public gateway
echo   0 Exit
echo.
set /p "CHOICE=Select: "
if "%CHOICE%"=="1" set "ACTION=restart"
if "%CHOICE%"=="2" set "ACTION=status"
if "%CHOICE%"=="3" set "ACTION=publish"
if "%CHOICE%"=="4" set "ACTION=unpublish"
if "%CHOICE%"=="5" set "ACTION=stop"
if "%CHOICE%"=="0" exit /b 0
if "%ACTION%"=="" (
  echo Invalid selection.
  goto menu
)

:run
echo.
echo PUBLIC_LAUNCHER_VERSION=stockboard_public_live_v2_20260722
echo PUBLIC_ACTION=%ACTION%
set "SCRIPT=%~dp0scripts\stockboard_public_live_v2.ps1"
echo PUBLIC_SCRIPT=%SCRIPT%
if not exist "%SCRIPT%" (
  echo PUBLIC_ERROR=Script not found: %SCRIPT%
  set "RC=1"
  goto finish
)

rem Keep execution in this console so PowerShell errors cannot disappear in a
rem separately elevated window. Local gateway actions do not require elevation;
rem Tailscale reports a visible permission error if the installation requires it.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Action "%ACTION%"
set "RC=%ERRORLEVEL%"

:finish
if not "%RC%"=="0" (
  echo.
  echo StockBoard public gateway command finished with an error. RC=%RC%
  echo The detailed error above is intentionally kept visible.
  pause
  exit /b %RC%
)
if "%PAUSE_AT_END%"=="1" (
  echo.
  pause
)
exit /b 0
