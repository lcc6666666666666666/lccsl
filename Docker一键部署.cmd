@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\docker_deploy.ps1" %*
set "DEPLOY_EXIT=%ERRORLEVEL%"
pause
exit /b %DEPLOY_EXIT%
