@echo off
setlocal EnableExtensions
cd /d C:\aiTrade

rem Safe opening-burst defaults. The internal universe remains up to 300/filter 100~200;
rem only the 32-bit realtime registration is capped at 100 until 09:00 validation.
if not defined STOCKBOARD_V2_COLLECTOR_LIMIT set "STOCKBOARD_V2_COLLECTOR_LIMIT=100"
if not defined STOCKBOARD_TRADE_VALUE_SAMPLE_MS set "STOCKBOARD_TRADE_VALUE_SAMPLE_MS=500"
if not defined STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS set "STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS=500"
if not defined STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS set "STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS=2000"
if not defined STOCKBOARD_BACKGROUND_REBUILD_POLL_MS set "STOCKBOARD_BACKGROUND_REBUILD_POLL_MS=50"
if not defined STOCKBOARD_STATUS_WRITE_INTERVAL_SEC set "STOCKBOARD_STATUS_WRITE_INTERVAL_SEC=5"

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
set "SAFE=%~dp0scripts\stockboard_v2_large_safe.ps1"
set "PREFLIGHT=%~dp0scripts\stockboard_v2_openapi_preflight.ps1"

if /I "%ACTION%"=="start" (
  set "START_ACTION=start"
  goto prepare_start
)
if /I "%ACTION%"=="restart" (
  set "START_ACTION=start"
  goto prepare_start
)
if /I "%ACTION%"=="start-fast" (
  set "START_ACTION=start-fast"
  goto prepare_start
)
if /I "%ACTION%"=="restart-fast" (
  set "START_ACTION=start-fast"
  goto prepare_start
)

goto direct_action

:prepare_start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SAFE%" -Action stop
if errorlevel 1 goto failed

rem Prevent an old base/singleflight writer from overwriting the shared status file.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$rows=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue ^| Where-Object { [string]$_.CommandLine -match 'context_snapshot_writer(_base^|_singleflight)?\.py' }); foreach($row in $rows){ Write-Host ('Stopping context writer PID=' + $row.ProcessId); Stop-Process -Id ([int]$row.ProcessId) -Force -ErrorAction SilentlyContinue }; if($rows.Count -gt 0){ Start-Sleep -Milliseconds 500 }"
if errorlevel 1 goto failed

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PREFLIGHT%"
if errorlevel 1 goto failed

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SAFE%" -Action "%START_ACTION%"
set "RC=%ERRORLEVEL%"
goto finish

:direct_action
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SAFE%" -Action "%ACTION%"
set "RC=%ERRORLEVEL%"
goto finish

:failed
set "RC=%ERRORLEVEL%"

:finish
if not "%RC%"=="0" (
  echo.
  echo StockBoard v2 safe launcher finished with an error.
  pause
)
exit /b %RC%
