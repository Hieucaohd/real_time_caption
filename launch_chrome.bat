@echo off
REM ============================================================
REM  Mo Chrome voi che do remote-debugging + profile rieng.
REM  Lan dau chay: dang nhap ca 4 site (ChatGPT, Gemini,
REM  NotebookLM, Claude) vao cua so nay. Session se duoc luu lai.
REM ============================================================

cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  call setup_web.bat
  if errorlevel 1 goto :web_error
)
if not exist web\dist\index.html (
  call setup_web.bat
  if errorlevel 1 goto :web_error
)
.venv\Scripts\python.exe -c "import fastapi, uvicorn, multipart" >nul 2>&1
if errorlevel 1 call setup_web.bat
if errorlevel 1 goto :web_error
.venv\Scripts\python.exe web_main.py --ensure
if errorlevel 1 goto :web_error
.venv\Scripts\python.exe web_main.py --open
if errorlevel 1 goto :web_error

set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
set PROFILE_DIR=%~dp0chrome-profile
set PORT=9222

echo Dang mo Chrome (port %PORT%, profile: %PROFILE_DIR%)...
%CHROME% ^
  --remote-debugging-port=%PORT% ^
  --user-data-dir="%PROFILE_DIR%" ^
  --no-first-run ^
  --no-default-browser-check ^
  https://gemini.google.com https://notebooklm.google.com https://claude.ai https://translate.google.com.vn
exit /b 0

:web_error
echo Could not start Real-time Caption Web. Run setup_web.bat or check caption-web.log.
pause
exit /b 1
