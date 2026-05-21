@echo off
setlocal
cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=8000"
set "API_URL=http://%HOST%:%PORT%"
set "BACKEND_LOG=runtime\logs\one_click_backend.log"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

call :resolve_python
if errorlevel 1 (
    echo.
    echo No working Python interpreter was found.
    echo Checked project venv, py launcher, python command, and common Python install paths.
    pause
    exit /b 1
)

"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing '%API_URL%/api/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    if not exist "runtime\logs" mkdir "runtime\logs"
    start "" /b cmd /c "call ""%PYTHON_EXE%"" %PYTHON_ARGS% -m backend >> ""%BACKEND_LOG%"" 2>&1"
    "%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(15); while((Get-Date) -lt $deadline){ try { Invoke-WebRequest -UseBasicParsing '%API_URL%/api/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { Start-Sleep -Milliseconds 400 } }; exit 1"
    if errorlevel 1 (
        echo.
        echo Backend launch failed. Check %BACKEND_LOG%.
        pause
        exit /b 1
    )
)

start "" "%API_URL%"
exit /b 0

:resolve_python
if exist "%~dp0.venv\Scripts\python.exe" (
    call :use_candidate "%~dp0.venv\Scripts\python.exe"
    if not errorlevel 1 exit /b 0
)

if exist "%~dp0venv\Scripts\python.exe" (
    call :use_candidate "%~dp0venv\Scripts\python.exe"
    if not errorlevel 1 exit /b 0
)

py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_EXE=py"
    set "PYTHON_ARGS=-3"
    exit /b 0
)

python -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_EXE=python"
    set "PYTHON_ARGS="
    exit /b 0
)

for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python*\python.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python*\python.exe"
    "%USERPROFILE%\anaconda3\python.exe"
    "%ProgramData%\Anaconda3\python.exe"
    "C:\Anaconda3\python.exe"
    "D:\Anaconda3\python.exe"
) do (
    if exist "%%~fP" (
        call :use_candidate "%%~fP"
        if not errorlevel 1 exit /b 0
    )
)

exit /b 1

:use_candidate
"%~1" -c "import sys" >nul 2>nul
if errorlevel 1 exit /b 1
set "PYTHON_EXE=%~1"
set "PYTHON_ARGS="
exit /b 0
