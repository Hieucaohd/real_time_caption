@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || goto :error
)
.venv\Scripts\python.exe -m pip install -r requirements-web.txt || goto :error
where npm.cmd >nul 2>&1 || goto :node_missing
pushd web
call npm.cmd ci
if errorlevel 1 (popd & goto :error)
call npm.cmd run build
if errorlevel 1 (popd & goto :error)
popd
echo Web setup complete. Run launch_chrome.bat.
exit /b 0
:node_missing
echo Install Node.js 22.12+ and run setup_web.bat again.
exit /b 1
:error
echo Web setup failed. Check the error above.
exit /b 1
