@echo off
rem The native EXE shares the persistent 15-second ChatGPT tab-opening gate.
cd /d "%~dp0"
start "" "%~dp0RealTimeCaption.exe" --open-chatgpt-browser
