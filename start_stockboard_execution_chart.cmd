@echo off
setlocal
cd /d "%~dp0"
set STOCKBOARD_EXEC_CHART_PORT=8010
python stockboard_execution_chart_server.py
pause
