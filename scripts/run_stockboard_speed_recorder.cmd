@echo off
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   StockBoard Speed Recorder Only
echo ============================================================
echo This recorder does not start or stop StockBoard.
echo It only polls an already-running StockBoard server and writes JSONL logs.
echo Press Ctrl+C to stop.
echo.

python scripts\stockboard_speed_recorder.py --interval 1 --timeout 1.2

echo.
echo Speed recorder stopped.
pause
