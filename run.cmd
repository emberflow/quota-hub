@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
)
if not exist .venv\Scripts\pythonw.exe exit /b 1
start "" /b ".venv\Scripts\pythonw.exe" launch.py
exit /b 0
