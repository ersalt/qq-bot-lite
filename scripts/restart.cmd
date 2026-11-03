@echo off
rem Restart the bot without leaving orphan console windows behind.
rem It asks the running instance to stop (POST /api/shutdown), waits for the
rem port to free, then opens a fresh window. A graceful exit code 0 makes
rem start.cmd close its own window, so repeated restarts do not pile up.
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8790"

echo [restart] asking the running instance to stop (port %PORT%) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue';" ^
  "foreach ($p in @('127.0.0.1','localhost')) {" ^
  "  try { Invoke-WebRequest -Method POST -Uri (\"http://{0}:%PORT%/api/shutdown\" -f $p) -TimeoutSec 3 -UseBasicParsing | Out-Null; break } catch {} }" ^
  "for ($i=0; $i -lt 40; $i++) {" ^
  "  $conn = Get-NetTCPConnection -State Listen -LocalPort %PORT% -ErrorAction SilentlyContinue;" ^
  "  if (-not $conn) { exit 0 };" ^
  "  Start-Sleep -Milliseconds 250 };" ^
  "exit 1"

if errorlevel 1 (
    echo [restart] port %PORT% is still busy, forcing the process tree down ...
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
        taskkill /PID %%P /T /F >nul 2>&1
    )
    powershell -NoProfile -Command "Start-Sleep -Milliseconds 800"
)

echo [restart] starting a new window ...
start "qq-bot-lite" cmd /c ""%~dp0..\start.cmd""
endlocal
