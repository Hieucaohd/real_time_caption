@echo off
rem Opens Google Chrome with remote debugging on port 9222, which Real-time Caption uses
rem to talk to ChatGPT (Summarize, Chat, New words). The first time, log in to ChatGPT in
rem this window; the login is kept in the chrome-profile folder next to this file.
setlocal
set PORT=9222
set PROFILE_DIR=%~dp0chrome-profile
set CHROME=
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe
if not defined CHROME if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
if not defined CHROME if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe
if not defined CHROME (
    echo Google Chrome was not found. Install it from https://www.google.com/chrome/ and try again.
    pause
    exit /b 1
)
start "" "%CHROME%" --remote-debugging-port=%PORT% --user-data-dir="%PROFILE_DIR%" --no-first-run --no-default-browser-check https://chatgpt.com
