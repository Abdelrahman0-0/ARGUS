@echo off
setlocal
cd /d "%~dp0"
title ARGUS build

echo ============================================================
echo  ARGUS build  (run this on Windows, from the extracted folder)
echo ============================================================
echo.

if not exist "gui_app.py" (
    echo [ERROR] gui_app.py not found next to build.bat.
    echo You probably ran this from inside the ZIP. Extract the ZIP first
    echo ^(right-click ^> Extract All^), then run build.bat from the new folder.
    goto :fail
)

REM ---- find Python (py launcher first, then python) ----
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python was not found.
    echo Install Python 3.10-3.13 from python.org and tick "Add python.exe to PATH".
    goto :fail
)
echo Using: %PY%
%PY% --version
%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required.
    goto :fail
)
echo.

set "LOG=%~dp0build_log.txt"
echo Build started > "%LOG%"

echo [1/3] Creating clean build environment + installing dependencies ^(first time takes a few minutes^)...
REM A clean venv avoids clashes with other packages on your PC (e.g. PyQt5 next to PySide6).
if not exist ".buildenv\Scripts\python.exe" (
    %PY% -m venv .buildenv >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Could not create the build environment.
        goto :showlog
    )
)
set "PY=.buildenv\Scripts\python.exe"
%PY% -m pip install --upgrade pip >> "%LOG%" 2>&1
%PY% -m pip install -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Installing requirements failed.
    goto :showlog
)
%PY% -m pip install --upgrade pyinstaller >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Installing PyInstaller failed.
    goto :showlog
)

echo [2/3] Building ARGUS.exe ^(a few minutes, please wait^)...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name ARGUS ^
    --icon "app_icon.ico" ^
    --add-data "app_icon.ico;." ^
    --add-data "remote_ui;remote_ui" ^
    --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide2 ^
    --exclude-module tkinter --exclude-module matplotlib ^
    --collect-submodules pynput ^
    --hidden-import win32gui ^
    --hidden-import win32process ^
    --hidden-import win32com.client ^
    --collect-all pywebpush ^
    --collect-all py_vapid ^
    --collect-all http_ece ^
    gui_app.py >> "%LOG%" 2>&1

if not exist "dist\ARGUS.exe" (
    echo [ERROR] PyInstaller did not produce dist\ARGUS.exe
    goto :showlog
)

echo [3/3] Copying ARGUS.exe next to this script...
copy /Y "dist\ARGUS.exe" "ARGUS.exe" >nul
echo.
echo ==========================================================
echo   ARGUS.exe is ready in this folder.
echo   If it disappears, Windows Defender quarantined it
echo   ^(it listens to keyboard/mouse, which looks suspicious^).
echo   Add this folder to Defender exclusions, or just use Run_ARGUS.bat.
echo ==========================================================
goto :end

:showlog
echo.
echo ---- last lines of build_log.txt ----
powershell -NoProfile -Command "Get-Content -Tail 30 -LiteralPath '%LOG%'"
echo -------------------------------------
echo Full log: %LOG%
echo Send me that text and I will fix it. Meanwhile Run_ARGUS.bat works without building.

:fail
:end
echo.
if /i not "%~1"=="nopause" pause
endlocal
