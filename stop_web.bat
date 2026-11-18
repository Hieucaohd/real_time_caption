@echo off
setlocal
title Stop Real-time Caption Web
echo Stopping the web backend on port 8765...
echo.
powershell.exe -NoLogo -NoProfile -Command "$ErrorActionPreference = 'Stop'; try { $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq 8765 } | Select-Object -ExpandProperty OwningProcess -Unique); if ($listeners.Count -eq 0) { Write-Host 'The web backend is already stopped.'; exit 0 }; foreach ($backendId in $listeners) { $process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $backendId); if (-not $process) { continue }; if ($process.CommandLine -notmatch '(?i)web_main\.py\b' -and $process.Name -notmatch '(?i)^RealTimeCaptionWeb.*\.exe$') { throw ('Port 8765 belongs to another or unrecognized app (PID ' + $backendId + '). Nothing was stopped for this process.') }; Stop-Process -Id $backendId -ErrorAction Stop; Write-Host ('Stopped web backend (PID ' + $backendId + ').') }; exit 0 } catch { Write-Host ('Could not stop backend: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }"
set "STOP_RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %STOP_RESULT%
