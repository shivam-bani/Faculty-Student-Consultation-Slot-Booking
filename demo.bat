@echo off
rem CampusSync presentation mode: fresh demo data around the current time, 1-minute aging, app started.
rem Double-click shortly before presenting. Close this window to stop the app.
cd /d "%~dp0"
title CampusSync DEMO server

sc query MySQL84 | find "RUNNING" >nul
if errorlevel 1 (
  echo MySQL is not running. Start the "MySQL84" service in Windows Services, then try again.
  pause
  exit /b 1
)

echo Creating fresh demo data...
".venv\Scripts\python.exe" -m flask --app app seed-demo
if errorlevel 1 (
  pause
  exit /b 1
)

start "" notepad demo-logins.txt
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:5000/"

rem Demo setting for this run only: priority rises every minute (.env is not changed).
set AGING_MINUTES=1
echo Starting CampusSync DEMO at http://127.0.0.1:5000/  (close this window to stop)
".venv\Scripts\python.exe" app.py
pause
