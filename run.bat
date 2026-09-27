@echo off
rem Launch Real-time Caption without a console window. Errors go to caption.log.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo Run setup.bat first.
    pause
    exit /b 1
)
start "" .venv\Scripts\pythonw.exe main.py
