@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."

python "%REPO_ROOT%\tools\aitrade_agent.py" %*
exit /b %ERRORLEVEL%
