@echo off
rem CampusSync: double-click to start the app. Close this window to stop it.
cd /d "%~dp0"
title CampusSync server

sc query MySQL84 | find "RUNNING" >nul
if errorlevel 1 (
  echo MySQL is not running. Start the "MySQL84" service in Windows Services, then try again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo The .venv folder is missing. See "Setup" in README.md.
  pause
  exit /b 1
)

rem Open the browser a few seconds from now, once the server is up.
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:5000/"

echo Starting CampusSync at http://127.0.0.1:5000/  (close this window to stop)
".venv\Scripts\python.exe" app.py
pause
