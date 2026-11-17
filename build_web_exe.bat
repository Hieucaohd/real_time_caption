@echo off
setlocal
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
for /f %%T in ('powershell.exe -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss-fff"') do set "RTC_BUILD_TAG=web-build-%%T"
if not defined RTC_BUILD_TAG goto :error
set "RTC_BUILD_BRANCH=build/%RTC_BUILD_TAG%"
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
.venv\Scripts\python.exe packaging\build_web_exe.py %* || goto :error
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
        echo Done: %~dp0dist\RealTimeCaptionWeb.exe
        echo Built from tag: %RTC_BUILD_TAG%
        echo Build branch kept: %RTC_BUILD_BRANCH%
        echo Double-click the EXE to open the web app in Chrome 9222.
    )
    if not defined RTC_BUILD_NO_PAUSE pause
    exit /b %RTC_BUILD_RESULT%
)
