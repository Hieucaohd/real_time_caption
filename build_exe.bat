@echo off
rem Build the portable app folder dist\RealTimeCaption (see packaging\build_exe.py).
rem   build_exe.bat                    CPU build
rem   build_exe.bat --gpu --zip        GPU build, zipped
rem   build_exe.bat --with-models      also bundle the downloaded Whisper models
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run setup.bat first.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m pip install -q -r requirements-build.txt || goto :error
.venv\Scripts\python.exe packaging\build_exe.py %* || goto :error
echo.
echo Done. The app is in dist\RealTimeCaption\RealTimeCaption.exe
pause
exit /b 0

:error
echo.
echo Build failed - see the messages above.
pause
exit /b 1
