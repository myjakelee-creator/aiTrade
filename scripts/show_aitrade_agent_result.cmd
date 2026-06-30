@echo off
setlocal

chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.." >nul

python tools\aitrade_agent.py --show-result
set "AGENT_EXIT_CODE=%ERRORLEVEL%"

popd >nul
exit /b %AGENT_EXIT_CODE%
