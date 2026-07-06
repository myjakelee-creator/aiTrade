@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   StockBoard Live + Program Net Sidecar
echo ============================================================
echo.
echo This launcher starts StockBoard first, then starts the 프로(억) sidecar
echo hidden in the background. If the sidecar fails, StockBoard keeps running.
echo.

if /I "%~1"=="stop" goto stop_all
if /I "%~1"=="restart" goto restart_all

:start_all
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
echo Starting Program Net Sidecar hidden in background...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_stockboard_program_net_sidecar_hidden.ps1"
set SIDECAR_EXIT=%ERRORLEVEL%

if not "%SIDECAR_EXIT%"=="0" (
  echo.
  echo Program net sidecar start returned code %SIDECAR_EXIT%.
  echo StockBoard is still running. Review the sidecar log messages above.
  echo.
  pause
  exit /b %SIDECAR_EXIT%
)

echo.
echo StockBoard and hidden Program Net Sidecar start commands completed.
echo This launcher window will close soon. The sidecar keeps running hidden.
echo.
timeout /t 4 /nobreak >nul
exit /b 0

:restart_all
call "%~f0" stop
call "%~f0"
exit /b %ERRORLEVEL%

:stop_all
echo.
echo == Stopping Program Net Sidecar ==
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_stockboard_program_net_sidecar.ps1"
echo.
echo == Stopping StockBoard ==
call stockboard_live.cmd stop
exit /b %ERRORLEVEL%
