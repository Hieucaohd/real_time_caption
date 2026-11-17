@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
set "RTC_BUILD_RESULT=1"
set "RTC_BUILD_SWITCHED="
echo Building Real-time Caption Web...
where git.exe >nul 2>&1 || goto :git_missing
where npm.cmd >nul 2>&1 || goto :node_missing
git rev-parse --is-inside-work-tree >nul 2>&1 || goto :git_missing
git show-ref --verify --quiet refs/heads/main
if errorlevel 1 (
    echo The local main branch is missing. Create or fetch it before building.
    goto :error
)
git status --porcelain --untracked-files=normal >nul || goto :error
set "RTC_BUILD_DIRTY="
for /f "delims=" %%S in ('git status --porcelain --untracked-files^=normal') do set "RTC_BUILD_DIRTY=1"
if defined RTC_BUILD_DIRTY (
    echo Commit or move your uncommitted files before building.
    echo The EXE must be built from exactly the commit recorded by its tag.
    goto :error
)
set "RTC_BUILD_TAG="
set "RTC_BUILD_VERSION="
set /p "RTC_BUILD_VERSION=Enter version (example: 1.2.3 or 1.2.3.4): "
rem Read untrusted input through the environment, never as a shell command.
powershell.exe -NoProfile -Command "$v=$env:RTC_BUILD_VERSION; if (-not $v) { exit 1 }; $v=$v.Trim(); if ($v -cnotmatch '\Av?(0|[1-9][0-9]{0,4})(\.(0|[1-9][0-9]{0,4})){2,3}\z') { exit 1 }; foreach ($part in $v.TrimStart('v').Split('.')) { if ([int]$part -gt 65535) { exit 1 } }; exit 0"
if errorlevel 1 (
    echo Invalid version. Use 3 or 4 numbers separated by dots, each from 0 to 65535.
    goto :error
)
for /f %%T in ('powershell.exe -NoProfile -Command "'v' + $env:RTC_BUILD_VERSION.Trim().TrimStart('v')"') do set "RTC_BUILD_TAG=%%T"
if not defined RTC_BUILD_TAG goto :error
set "RTC_BUILD_BRANCH=build/%RTC_BUILD_TAG%"
git show-ref --verify --quiet "refs/tags/%RTC_BUILD_TAG%"
if not errorlevel 1 (
    echo Tag %RTC_BUILD_TAG% already exists. Run again with a new version.
    goto :error
)
git show-ref --verify --quiet "refs/heads/%RTC_BUILD_BRANCH%"
if not errorlevel 1 (
    echo Branch %RTC_BUILD_BRANCH% already exists. Run again with a new version.
    goto :error
)
echo Creating tag %RTC_BUILD_TAG% and branch %RTC_BUILD_BRANCH%...
git tag -a "%RTC_BUILD_TAG%" -m "Web EXE build %RTC_BUILD_TAG%" || goto :error
git checkout -b "%RTC_BUILD_BRANCH%" "%RTC_BUILD_TAG%" || goto :error
set "RTC_BUILD_SWITCHED=1"

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
.venv\Scripts\python.exe packaging\build_web_exe.py %* --build-tag "%RTC_BUILD_TAG%" || goto :error
set "RTC_BUILD_RESULT=0"
goto :finish
:git_missing
echo Git is required. Run this script from a Git checkout of the project.
goto :error
:node_missing
echo Install Node.js 22.12+ with npm, then run this file again.
goto :error
:error
echo.
echo Build failed. Read the error above. See build\web-exe\build.log if packaging started.
:finish
rem Parse all cleanup commands before checkout can replace this batch file.
(
    if defined RTC_BUILD_SWITCHED (
        echo Returning to main...
        git checkout main
        if errorlevel 1 (
            echo Could not return to main. Your files were not forced or discarded.
            echo Resolve the Git error above, then run: git checkout main
            if not defined RTC_BUILD_NO_PAUSE pause
            exit /b 1
        )
    )
    if "%RTC_BUILD_RESULT%"=="0" (
        echo.
        echo Done: %~dp0dist\RealTimeCaptionWeb-%RTC_BUILD_TAG%.exe
        echo Built from tag: %RTC_BUILD_TAG%
        echo Build branch kept: %RTC_BUILD_BRANCH%
        echo Double-click the EXE to open the web app in Chrome 9222.
    )
    if not defined RTC_BUILD_NO_PAUSE pause
    exit /b %RTC_BUILD_RESULT%
)
