@echo off
setlocal EnableDelayedExpansion

chcp 65001 >nul

if "%~1"=="" (
    echo Usage: scripts\run_aitrade_agent_goal.cmd ^<goal^> [readonly^|agent^|price-lane^|stockboard-ui] [agent options]
    exit /b 2
)

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.." >nul

set "AGENT_GOAL=%~1"
shift /1

if "%~1"=="" (
    set "AGENT_PROFILE=readonly"
) else (
    set "NEXT_ARG=%~1"
    if "!NEXT_ARG:~0,2!"=="--" (
        set "AGENT_PROFILE=readonly"
    ) else (
        set "AGENT_PROFILE=%~1"
        shift /1
    )
)

set "FORWARD_ARGS="
:collect_args
if "%~1"=="" goto run_agent
set "FORWARD_ARGS=!FORWARD_ARGS! "%~1""
shift /1
goto collect_args

:run_agent
python tools\aitrade_agent.py --goal "%AGENT_GOAL%" --goal-profile "%AGENT_PROFILE%" !FORWARD_ARGS!
set "AGENT_EXIT_CODE=%ERRORLEVEL%"

echo aiTrade Local Agent exit code: %AGENT_EXIT_CODE%

popd >nul
exit /b %AGENT_EXIT_CODE%
