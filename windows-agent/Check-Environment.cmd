@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Check-Environment.ps1" -Pause
set "agent_check_exit=%ERRORLEVEL%"
exit /b %agent_check_exit%
