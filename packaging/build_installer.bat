@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

set "ISS=packaging\CampusNetAutoLogin.iss"
set "OUTDIR=packaging\output"

if not exist "dist\CampusNetAutoLogin\CampusNetAutoLogin.exe" (
    echo [ERROR] Build the exe first: packaging\build_exe.bat
    pause
    exit /b 1
)

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
    echo [ERROR] Inno Setup 6 not found.
    pause
    exit /b 1
)

if not exist "%OUTDIR%" mkdir "%OUTDIR%"

echo Using ISCC: !ISCC!
echo Compiling installer ...
"!ISCC!" "%ISS%"
if errorlevel 1 (
    echo [ERROR] Inno Setup compile failed.
    pause
    exit /b 1
)

echo.
echo Installer finished:
for %%F in ("%OUTDIR%\CampusNetAutoLogin_Setup_v*.exe") do (
    echo   %%~fF
    echo   size: %%~zF bytes
)
pause
