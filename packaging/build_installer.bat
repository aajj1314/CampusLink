@echo off
REM ===============================================================
REM  Build the Windows installer with Inno Setup 6.
REM
REM  Output: packaging\output\CampusLink_Setup_v1.0.exe
REM
REM  Requires Inno Setup 6 (ISCC.exe). If it is missing this script
REM  prints the download link and stops -- it will NOT try to install
REM  anything by itself.
REM
REM  Keep this file ASCII-only (see build_exe.bat for the reason).
REM ===============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

set "ISS=packaging\CampusLink.iss"
set "OUTDIR=packaging\output"

echo ==============================================================
echo  CampusLink installer
echo ==============================================================
echo.

if not exist "dist\CampusLink\CampusLink.exe" (
    echo [ERROR] dist\CampusLink\CampusLink.exe not found.
    echo         Build the app first:  packaging\build_exe.bat
    echo.
    pause
    exit /b 1
)

REM ---- locate ISCC.exe ----
set "ISCC="
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
    "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
) do (
    if exist %%P if not defined ISCC set "ISCC=%%~P"
)

if not defined ISCC (
    where ISCC.exe >nul 2>nul && set "ISCC=ISCC.exe"
)

if not defined ISCC (
    echo [ERROR] Inno Setup 6 not found.
    echo.
    echo   Download and install it, then run this script again:
    echo     https://jrsoftware.org/isdl.php
    echo.
    echo   Direct download ^(6.x^):
    echo     https://files.jrsoftware.org/is/6/innosetup-6.4.3.exe
    echo.
    echo   Silent install if you prefer:
    echo     innosetup-6.4.3.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
    echo.
    echo   The .iss script itself is ready at:
    echo     %CD%\%ISS%
    echo.
    pause
    exit /b 1
)

echo  ISCC: !ISCC!
echo.

if not exist "%OUTDIR%" mkdir "%OUTDIR%"

echo  Compiling ...
"!ISCC!" "%ISS%"
if errorlevel 1 (
    echo.
    echo [ERROR] Inno Setup compile failed. Read the messages above.
    pause
    exit /b 1
)

echo.
echo ==============================================================
echo  Installer finished
echo ==============================================================
for %%F in ("%OUTDIR%\CampusLink_Setup_v*.exe") do (
    echo   %%~fF
    echo   size: %%~zF bytes
)
echo.
echo  Next: test it in a clean Windows environment, or at least a
echo        machine that never had Python installed.
echo.

if /i "%~1"=="--open" explorer "%CD%\%OUTDIR%"

pause
