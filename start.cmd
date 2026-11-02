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

".venv\Scripts\python.exe" -m app %*
set "EXITCODE=%errorlevel%"

if not "%EXITCODE%"=="0" (
    echo.
    echo [exit] code %EXITCODE% - see logs\app.log
    echo Press any key to close this window...
    pause >nul
)
goto :eof

:fail
echo.
echo [fail] setup failed. Check that Python is in PATH.
pause
