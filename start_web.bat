@echo off
setlocal
cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=8000"
set "API_URL=http://%HOST%:%PORT%"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=py -3"
) else (
    set "PY_CMD=python"
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing '%API_URL%/api/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    if not exist "runtime\logs" mkdir "runtime\logs"
    start "" /b cmd /c "%PY_CMD% -m backend > ""runtime\logs\one_click_backend.log"" 2>&1"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(15); while((Get-Date) -lt $deadline){ try { Invoke-WebRequest -UseBasicParsing '%API_URL%/api/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { Start-Sleep -Milliseconds 400 } }; exit 1"
    if errorlevel 1 (
        echo.
        echo Backend launch failed. Check runtime\logs\one_click_backend.log.
        pause
        exit /b 1
    )
)

start "" "%API_URL%"
