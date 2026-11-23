@echo off
rem Runs the model with whichever Python has its libraries; installs them on first use.
rem Usage from this folder:  betmodel daily | betmodel picks | betmodel nfl week | betmodel soccer predict Arsenal Chelsea
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Get it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then run this again.
  exit /b 1
)
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
"%PY%" -c "import numpy, scipy, pandas, shiny" >nul 2>nul
if errorlevel 1 call :setup
if not exist "config.json" copy "config.example.json" "config.json" >nul
"%PY%" -m betmodel -q %*
exit /b %errorlevel%

:setup
echo First run: installing numpy, scipy, pandas and shiny (takes a minute)...
if exist ".venv" rmdir /s /q ".venv"
python -m venv .venv >nul 2>nul
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt >nul 2>nul
  ".venv\Scripts\python.exe" -c "import numpy, scipy, pandas, shiny" >nul 2>nul
  if not errorlevel 1 (
    set "PY=.venv\Scripts\python.exe"
    exit /b 0
  )
)
if exist ".venv" rmdir /s /q ".venv"
echo (using your main Python installation instead of a private environment)
python -m pip install --user --quiet --disable-pip-version-check -r requirements.txt
set "PY=python"
exit /b 0
