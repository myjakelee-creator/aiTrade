@echo off
setlocal EnableExtensions
cd /d C:\aiTrade

rem Safe opening-burst defaults. The internal universe remains up to 300/filter 100~200;
rem only the verified 32-bit price collector is allowed in the production OpenAPI session.
if not defined STOCKBOARD_V2_COLLECTOR_LIMIT set "STOCKBOARD_V2_COLLECTOR_LIMIT=100"
if not defined STOCKBOARD_TRADE_VALUE_SAMPLE_MS set "STOCKBOARD_TRADE_VALUE_SAMPLE_MS=500"
if not defined STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS set "STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS=500"
if not defined STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS set "STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS=2000"
if not defined STOCKBOARD_BACKGROUND_REBUILD_POLL_MS set "STOCKBOARD_BACKGROUND_REBUILD_POLL_MS=50"
if not defined STOCKBOARD_STATUS_WRITE_INTERVAL_SEC set "STOCKBOARD_STATUS_WRITE_INTERVAL_SEC=5"
rem A second Kiwoom QAx realtime registration stopped the verified price feed.
rem Keep the large-trade sidecar production-disabled; files remain for isolated testing only.
if not defined STOCKBOARD_LARGE_TRADE_SIDECAR_ENABLED set "STOCKBOARD_LARGE_TRADE_SIDECAR_ENABLED=0"
rem A manually refreshed full Kiwoom catalog is preferred. If it is absent, the
rem ThemeMembershipLoader continues to the existing static 10-theme fallback.
if not defined STOCKBOARD_THEME_MEMBERSHIP_FILE set "STOCKBOARD_THEME_MEMBERSHIP_FILE=C:\aiTrade\data\runtime\stockboard_v2\theme_membership.json"

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
echo   8 Data consistency doctor
echo   9 Price compare doctor
echo  10 Price path trace 15s
echo  11 QAx collector trace 15s
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
if "%CHOICE%"=="8" set "ACTION=data-doctor"
if "%CHOICE%"=="9" set "ACTION=price-doctor"
if "%CHOICE%"=="10" set "ACTION=price-trace"
if "%CHOICE%"=="11" set "ACTION=collector-trace"
if "%CHOICE%"=="0" exit /b 0
if "%ACTION%"=="" (
  echo Invalid selection.
  goto menu
)

:run
set "SAFE=%~dp0scripts\stockboard_v2_large_safe.ps1"
set "PREFLIGHT=%~dp0scripts\stockboard_v2_openapi_preflight.ps1"
set "PY64_DEPS=%~dp0scripts\stockboard_v2_python64_dependencies.ps1"
set "LARGE_TRADE_SIDECAR=%~dp0scripts\stockboard_large_trade_sidecar.ps1"
set "EXECUTION_DOCTOR=%~dp0scripts\stockboard_execution_strength_doctor.ps1"
set "DATA_CONSISTENCY=%~dp0scripts\stockboard_v2_data_consistency.ps1"
set "PRICE_DOCTOR=%~dp0scripts\stockboard_v2_price_compare.py"
set "PRICE_TRACE=%~dp0scripts\stockboard_v2_price_trace.py"
set "COLLECTOR_TRACE=%~dp0scripts\stockboard_v2_collector_trace.py"

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
if /I "%ACTION%"=="data-doctor" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%DATA_CONSISTENCY%"
  set "RC=%ERRORLEVEL%"
  goto finish
)
if /I "%ACTION%"=="price-doctor" (
  py -3 "%PRICE_DOCTOR%"
  set "RC=%ERRORLEVEL%"
  goto finish
)
if /I "%ACTION%"=="price-trace" (
  py -3 "%PRICE_TRACE%" --duration-sec 15
  set "RC=%ERRORLEVEL%"
  goto finish
)
if /I "%ACTION%"=="collector-trace" (
  py -3 "%COLLECTOR_TRACE%" --duration-sec 15
  set "RC=%ERRORLEVEL%"
  goto finish
)

goto direct_action

:prepare_start
rem Always terminate any experimental second QAx owner before touching production.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LARGE_TRADE_SIDECAR%" -Action stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SAFE%" -Action stop
if errorlevel 1 goto failed

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PREFLIGHT%"
if errorlevel 1 goto failed

rem Ensure the 64-bit worker can open the Kiwoom REST WebSocket used by FID228.
rem The package is installed only when missing and verified before the worker starts.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PY64_DEPS%"
if errorlevel 1 goto failed

rem The safe launcher owns the single verified portable-v2 context writer.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SAFE%" -Action "%START_ACTION%"
set "SAFE_RC=%ERRORLEVEL%"
if not "%SAFE_RC%"=="0" goto failed_with_safe_rc

echo LARGE_TRADE_SIDECAR_PRODUCTION=disabled_due_to_price_feed_conflict
set "RC=0"
goto finish

:failed_with_safe_rc
set "RC=%SAFE_RC%"
goto finish

:direct_action
if /I "%ACTION%"=="stop" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LARGE_TRADE_SIDECAR%" -Action stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SAFE%" -Action "%ACTION%"
set "RC=%ERRORLEVEL%"
if /I "%ACTION%"=="status" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LARGE_TRADE_SIDECAR%" -Action status
if /I "%ACTION%"=="doctor" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LARGE_TRADE_SIDECAR%" -Action status
if /I "%ACTION%"=="doctor" if "%RC%"=="0" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%EXECUTION_DOCTOR%"
if /I "%ACTION%"=="doctor" if errorlevel 1 set "RC=1"
goto finish

:failed
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" set "RC=1"

:finish
if not "%RC%"=="0" (
  echo.
  echo StockBoard v2 safe launcher finished with an error.
  pause
)
exit /b %RC%
