@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe call setup.bat
.venv\Scripts\python.exe -m pip install -r requirements-web.txt || goto :error
echo.
echo Web setup complete. Start it with run_web.bat
pause
exit /b 0
:error
echo Web setup failed.
pause
exit /b 1
