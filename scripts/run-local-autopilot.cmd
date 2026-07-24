@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-local-autopilot.ps1" %*
exit /b %errorlevel%
