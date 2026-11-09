@echo off
setlocal
cd /d "%~dp0"
title ARGUS

if not exist "gui_app.py" (
    echo [ERROR] Extract the ZIP first, then run this from the extracted folder.
    pause
    exit /b 1
)

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python not found. Install Python 3.10-3.13 from python.org ^(tick Add to PATH^).
    pause
    exit /b 1
)

if not exist ".deps_ok" (
    echo Installing dependencies ^(first run only^)...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency install failed. Check your internet and try again.
        pause
        exit /b 1
    )
    echo ok> .deps_ok
)

%PY% gui_app.py
if errorlevel 1 (
    echo.
    echo [ERROR] ARGUS exited with an error ^(see above^).
    pause
)
endlocal
