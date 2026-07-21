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
goto elevate

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

:elevate
powershell.exe -NoProfile -Command "$p=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if($p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){exit 0}else{exit 1}"
if errorlevel 1 (
  powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%ACTION%','%PAUSE_AT_END%' -Verb RunAs"
  exit /b 0
)

set "SCRIPT=%~dp0scripts\stockboard_public_live.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Action "%ACTION%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo StockBoard public gateway command finished with an error.
)
if "%PAUSE_AT_END%"=="1" (
  echo.
  pause
)
exit /b %RC%
