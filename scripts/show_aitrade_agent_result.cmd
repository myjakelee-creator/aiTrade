@echo off
setlocal

chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.." >nul

if "%~1"=="" (
  python tools\aitrade_agent.py --show-result
) else (
  python tools\aitrade_agent.py %*
)
set "AGENT_EXIT_CODE=%ERRORLEVEL%"

popd >nul
exit /b %AGENT_EXIT_CODE%
