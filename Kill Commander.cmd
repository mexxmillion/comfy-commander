@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Commander Python environment is missing in this folder.
  pause
  exit /b 1
)
echo Stops this folder's Commander server and local workers. Running jobs may be interrupted.
echo ComfyUI and rentals keep running. Finish active work before continuing.
choice /C YN /N /M "Stop Commander now? [Y/N] "
if errorlevel 2 exit /b 0
".venv\Scripts\python.exe" -m backend.service_stop
set "commanderStopResult=%errorlevel%"
pause
exit /b %commanderStopResult%
