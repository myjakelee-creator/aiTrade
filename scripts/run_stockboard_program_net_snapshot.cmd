@echo off
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   StockBoard Program Net Sidecar
echo ============================================================
echo.
echo This sidecar does not start or stop StockBoard.
echo It only writes docs\assets\program_net_snapshot.json for the browser overlay.
echo Close this window or press Ctrl+C to stop.
echo.

python scripts\stockboard_program_net_snapshot.py --interval 60

echo.
echo Program net sidecar stopped.
pause
