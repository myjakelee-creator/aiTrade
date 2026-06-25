@echo off
setlocal
cd /d C:\aiTrade
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start_stockboard_live.ps1"
if errorlevel 1 (
  echo.
  echo StockBoard launcher failed. Press any key to close this launcher window.
  pause >nul
  exit /b 1
)

exit /b 0
