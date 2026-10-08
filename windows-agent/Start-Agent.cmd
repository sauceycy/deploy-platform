@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Agent.ps1" -Pause
set "agent_exit=%ERRORLEVEL%"
exit /b %agent_exit%
