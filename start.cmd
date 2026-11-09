@echo off
chcp 65001 >nul
title qq-bot-lite
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" goto setup

rem A venv copied from another computer can point at a missing interpreter.
"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 goto recreate

rem The venv exists but may be missing runtime packages (copied/incomplete).
"%PY%" -c "import uvicorn" >nul 2>&1
if errorlevel 1 goto deps

goto run

:recreate
echo [setup] .venv is not usable, recreating...
rmdir /s /q ".venv"
goto setup

:setup
echo [setup] creating venv and installing dependencies...
python -m venv .venv
if errorlevel 1 goto fail
"%PY%" -m pip install --upgrade pip -q
if errorlevel 1 goto fail
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto fail
goto run

:deps
echo [setup] installing missing dependencies into .venv...
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto fail
goto run

:run
rem A graceful stop exits 0, so this window closes on its own.
"%PY%" -m app %*
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
echo [fail] setup failed. Check that Python is in PATH and the network is available.
timeout /t 20 >nul 2>&1
