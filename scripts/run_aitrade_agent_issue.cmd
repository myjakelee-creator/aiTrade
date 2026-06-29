@echo off
setlocal EnableDelayedExpansion

chcp 65001 >nul

if "%~1"=="" (
    echo Usage: scripts\run_aitrade_agent_issue.cmd ^<issue_number^> [agent options]
    exit /b 2
)

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.." >nul

set "ISSUE_NUMBER=%~1"
shift /1

set "FORWARD_ARGS="
:collect_args
if "%~1"=="" goto run_agent
set "FORWARD_ARGS=!FORWARD_ARGS! "%~1""
shift /1
goto collect_args

:run_agent
python tools\aitrade_agent.py --issue "%ISSUE_NUMBER%" !FORWARD_ARGS!
set "AGENT_EXIT_CODE=%ERRORLEVEL%"

echo aiTrade Local Agent exit code: %AGENT_EXIT_CODE%

popd >nul
exit /b %AGENT_EXIT_CODE%
