@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run tools\first_run.ps1 once to install Commander.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m backend.service_start %*
if errorlevel 1 pause
