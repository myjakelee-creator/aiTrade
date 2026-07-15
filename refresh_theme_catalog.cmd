@echo off
setlocal EnableExtensions
cd /d C:\aiTrade

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\refresh_theme_catalog.ps1" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo Theme catalog refresh finished with an error.
  pause
)

exit /b %RC%
