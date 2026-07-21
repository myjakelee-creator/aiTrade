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
echo StockBoard v2 Public Launcher
echo.
echo   1 Start everything and publish - recommended after reboot
echo   2 Show all status
echo   3 Stop everything and disable public access
echo   4 Restart public gateway only
echo   5 Publish public gateway only
echo   6 Unpublish and restore private Tailscale Serve
echo   7 Stop public gateway only
echo   0 Exit
echo.
set /p "CHOICE=Select: "
if "%CHOICE%"=="1" set "ACTION=all-start"
if "%CHOICE%"=="2" set "ACTION=all-status"
if "%CHOICE%"=="3" set "ACTION=all-stop"
if "%CHOICE%"=="4" set "ACTION=restart"
if "%CHOICE%"=="5" set "ACTION=publish"
if "%CHOICE%"=="6" set "ACTION=unpublish"
if "%CHOICE%"=="7" set "ACTION=stop"
if "%CHOICE%"=="0" exit /b 0
if "%ACTION%"=="" (
  echo Invalid selection.
  goto menu
)

:run
echo.
echo PUBLIC_LAUNCHER_VERSION=stockboard_public_all_menu_v1_20260722
echo PUBLIC_ACTION=%ACTION%

if /I "%ACTION%"=="all-start" goto run_all
if /I "%ACTION%"=="all-status" goto run_all
if /I "%ACTION%"=="all-stop" goto run_all

goto run_gateway

:run_all
set "ALL_SCRIPT=%~dp0scripts\stockboard_public_all.ps1"
echo ALL_SCRIPT=%ALL_SCRIPT%
if not exist "%ALL_SCRIPT%" (
  echo PUBLIC_ERROR=Script not found: %ALL_SCRIPT%
  set "RC=1"
  goto finish
)

set "ALL_ACTION="
if /I "%ACTION%"=="all-start" set "ALL_ACTION=start"
if /I "%ACTION%"=="all-status" set "ALL_ACTION=status"
if /I "%ACTION%"=="all-stop" set "ALL_ACTION=stop"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ALL_SCRIPT%" -Action "%ALL_ACTION%"
set "RC=%ERRORLEVEL%"
goto finish

:run_gateway
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
  echo StockBoard public launcher finished with an error. RC=%RC%
  echo The detailed error above is intentionally kept visible.
  pause
  exit /b %RC%
)
if "%PAUSE_AT_END%"=="1" (
  echo.
  pause
)
exit /b 0
