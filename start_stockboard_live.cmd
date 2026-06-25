@echo off
setlocal
cd /d C:\aiTrade
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start_stockboard_live.ps1"
echo.
echo Press any key to close this launcher window.
pause >nul
