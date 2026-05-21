@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=py -3"
) else (
    set "PY_CMD=python"
)

%PY_CMD% gui.py

if errorlevel 1 (
    echo.
    echo Launch failed. Please check whether Python and project dependencies are installed.
    pause
)
