@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0replay\scripts\start_stockboard_replay.ps1"
