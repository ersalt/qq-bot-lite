@echo off
chcp 65001 >nul
title qq-bot-lite
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] creating venv and installing dependencies...
    python -m venv .venv
    if errorlevel 1 goto fail
    ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto fail
)

rem A graceful stop exits 0, so this window closes on its own.
".venv\Scripts\python.exe" -m app %*
set "EXITCODE=%errorlevel%"

if "%EXITCODE%"=="0" goto :eof

echo.
echo [exit] code %EXITCODE% - see logs\app.log
echo This window closes by itself in 10 seconds.
rem Bounded wait instead of "pause": when the process was force-killed the
rem window must not sit there waiting for a keypress (that is how orphan
rem windows pile up across restarts).
timeout /t 10 >nul 2>&1
goto :eof

:fail
echo.
echo [fail] setup failed. Check that Python is in PATH.
timeout /t 20 >nul 2>&1
