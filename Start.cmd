@echo off
cd /d "%~dp0"
python -c "import sys; from pathlib import Path; p=Path.cwd().parents[1]/'work'/'python-deps'; sys.path.insert(0,str(p)) if p.is_dir() else None; import tkinter; from scrapling.fetchers import Fetcher" >nul 2>&1
if not errorlevel 1 goto run_system
if exist ".venv\Scripts\python.exe" goto install
python -m venv .venv
if errorlevel 1 goto failed
:install
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe app.py
if errorlevel 1 goto failed
exit /b 0
:run_system
python app.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Unable to start. Install Python 3.10+ with tkinter and enable PATH, then try again.
pause
exit /b 1
