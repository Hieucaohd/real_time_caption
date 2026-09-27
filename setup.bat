@echo off
rem One-time setup: create a virtual environment and install dependencies.
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv || goto :error
)
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto :error
echo.
echo Setup complete. Start the app with run.bat
pause
exit /b 0

:error
echo.
echo Setup failed. Make sure Python 3.10+ is installed and on PATH.
pause
exit /b 1
