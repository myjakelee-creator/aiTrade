@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   StockBoard Live + Program Net Sidecar
echo ============================================================
echo.
echo This launcher starts StockBoard first, then starts the 프로(억) sidecar
echo in a separate window. If the sidecar fails, StockBoard keeps running.
echo.

call stockboard_live.cmd start
set STOCKBOARD_EXIT=%ERRORLEVEL%

if not "%STOCKBOARD_EXIT%"=="0" (
  echo.
  echo StockBoard start returned code %STOCKBOARD_EXIT%.
  echo Program net sidecar will not be started because the main board did not validate ready.
  echo.
  pause
  exit /b %STOCKBOARD_EXIT%
)

echo.
echo Starting Program Net Sidecar in a separate window...
start "StockBoard Program Net Sidecar" cmd /k "cd /d "%~dp0" && scripts\run_stockboard_program_net_snapshot.cmd"

echo.
echo StockBoard and Program Net Sidecar start commands completed.
echo - Main board: stockboard_live.cmd
echo - Sidecar: scripts\run_stockboard_program_net_snapshot.cmd
echo.
echo You may close this launcher window after confirming both windows are open.
echo.
pause
