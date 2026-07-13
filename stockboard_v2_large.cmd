@echo off
setlocal EnableExtensions
cd /d C:\aiTrade

set "ACTION=%~1"
if "%ACTION%"=="" goto menu
goto run

:menu
echo.
echo StockBoard v2 Safe Launcher
echo.
echo   1 Start normal
echo   2 Stop
echo   3 Restart normal
echo   4 Status
echo   5 Start fast-open
echo   6 Restart fast-open
echo   7 Doctor
echo   0 Exit
echo.
set /p "CHOICE=Select: "
if "%CHOICE%"=="1" set "ACTION=start"
if "%CHOICE%"=="2" set "ACTION=stop"
if "%CHOICE%"=="3" set "ACTION=restart"
if "%CHOICE%"=="4" set "ACTION=status"
if "%CHOICE%"=="5" set "ACTION=start-fast"
if "%CHOICE%"=="6" set "ACTION=restart-fast"
if "%CHOICE%"=="7" set "ACTION=doctor"
if "%CHOICE%"=="0" exit /b 0
if "%ACTION%"=="" (
  echo Invalid selection.
  goto menu
)

:run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stockboard_v2_large_safe.ps1" -Action "%ACTION%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo StockBoard v2 safe launcher finished with an error.
  pause
)
exit /b %RC%
