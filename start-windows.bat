@echo off
title VacancyWatch
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist ".venv\Scripts\python.exe" (
  echo First start: setting things up, please wait about a minute...
  %PY% -m venv .venv || (echo Python not found. Install it from python.org and tick "Add Python to PATH". & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || (echo Installation failed - check your internet connection. & pause & exit /b 1)
)
".venv\Scripts\python.exe" run.py %*
pause
