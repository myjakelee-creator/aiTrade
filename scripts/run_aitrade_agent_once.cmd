@echo off
setlocal EnableDelayedExpansion

chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.." >nul

if "%~1"=="" (
  set "PROMPT_FILE=data\runtime\aitrade_agent_task.txt"
) else (
  set "FIRST_ARG=%~1"
  if "!FIRST_ARG:~0,2!"=="--" (
    set "PROMPT_FILE=data\runtime\aitrade_agent_task.txt"
  ) else (
    set "PROMPT_FILE=%~1"
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
python tools\aitrade_agent.py --prompt-file "%PROMPT_FILE%" !FORWARD_ARGS!
set "AGENT_EXIT_CODE=%ERRORLEVEL%"

echo aiTrade Local Agent exit code: %AGENT_EXIT_CODE%
popd >nul
exit /b %AGENT_EXIT_CODE%
