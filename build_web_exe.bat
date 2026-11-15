@echo off
setlocal
cd /d "%~dp0"
echo Building Real-time Caption Web...
where npm.cmd >nul 2>&1 || goto :node_missing
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || goto :error
)
.venv\Scripts\python.exe -m pip install -r requirements-web.txt -r requirements-web-build.txt || goto :error
pushd web
call npm.cmd ci
if errorlevel 1 (popd & goto :error)
call npm.cmd run build
if errorlevel 1 (popd & goto :error)
popd
.venv\Scripts\python.exe packaging\build_web_exe.py %* || goto :error
echo.
echo Done: %~dp0dist\RealTimeCaptionWeb.exe
echo Double-click the EXE to open the web app in Chrome 9222.
if not defined RTC_BUILD_NO_PAUSE pause
exit /b 0
:node_missing
echo Install Node.js 22.12+ with npm, then run this file again.
goto :error
:error
echo.
echo Build failed. Read the error above. See build\web-exe\build.log if packaging started.
if not defined RTC_BUILD_NO_PAUSE pause
exit /b 1
