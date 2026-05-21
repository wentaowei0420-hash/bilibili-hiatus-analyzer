@echo off
setlocal
cd /d "%~dp0"

call :resolve_python
if errorlevel 1 (
    echo.
    echo No working Python interpreter was found.
    echo Checked project venv, py launcher, python command, and common Python install paths.
    pause
    exit /b 1
)

set "GUI_PYTHON_EXE=%PYTHON_EXE%"
call :prefer_pythonw

if defined PYTHON_ARGS (
    start "" "%GUI_PYTHON_EXE%" %PYTHON_ARGS% gui.py
) else (
    start "" "%GUI_PYTHON_EXE%" gui.py
)

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

:prefer_pythonw
for %%I in ("%PYTHON_EXE%") do (
    if /i "%%~nxI"=="python.exe" (
        if exist "%%~dpIpythonw.exe" (
            set "GUI_PYTHON_EXE=%%~dpIpythonw.exe"
        )
    )
)
exit /b 0
