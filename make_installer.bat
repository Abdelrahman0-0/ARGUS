@echo off
setlocal
cd /d "%~dp0"
title ARGUS - build the installer

echo ============================================================
echo  Builds ARGUS_Setup.exe  (a real installer for ARGUS)
echo  Takes a few minutes. Run from the extracted folder.
echo ============================================================
echo.

if not exist "installer\installer.py" (
    echo [ERROR] installer folder not found. Extract the ZIP first.
    goto :fail
)

echo [1/2] Building ARGUS.exe ...
if exist "ARGUS.exe" del /q "ARGUS.exe"
call build.bat nopause
if not exist "ARGUS.exe" (
    echo [ERROR] ARGUS.exe was not built. See build_log.txt
    goto :fail
)

set "PY=.buildenv\Scripts\python.exe"
set "LOG=%~dp0installer_build_log.txt"
echo Installer build > "%LOG%"
if not exist "installer_payload" mkdir "installer_payload"
copy /Y "ARGUS.exe" "installer_payload\ARGUS.exe" >nul

echo.
echo [2/2] Building ARGUS_Setup.exe ...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name ARGUS_Setup ^
    --icon "app_icon.ico" ^
    --add-data "app_icon.ico;." ^
    --add-data "installer_payload\ARGUS.exe;payload" ^
    --paths "installer" --paths "." ^
    --hidden-import win32com.client --hidden-import pythoncom --hidden-import pywintypes ^
    --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide2 ^
    --exclude-module tkinter --exclude-module matplotlib ^
    installer\installer.py >> "%LOG%" 2>&1

if not exist "dist\ARGUS_Setup.exe" (
    echo [ERROR] The installer was not built.
    powershell -NoProfile -Command "Get-Content -Tail 30 -LiteralPath '%LOG%'"
    echo Full log: %LOG%
    goto :fail
)
copy /Y "dist\ARGUS_Setup.exe" "ARGUS_Setup.exe" >nul
echo.
echo ==========================================================
echo   Done:  ARGUS_Setup.exe  (in this folder)
echo   Double-click it to install ARGUS like a normal program.
echo   Windows may warn about an unknown publisher - choose
echo   "More info" then "Run anyway" (the file is unsigned).
echo ==========================================================

:fail
echo.
pause
endlocal
