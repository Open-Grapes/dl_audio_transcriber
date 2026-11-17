@echo off
rem Runs dl_transcribe from source (after: py -3.13 -m venv .venv ^& .venv\Scripts\pip install -r requirements.txt)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0run.py" %*
