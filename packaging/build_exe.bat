@echo off
REM ===============================================================
REM  Build CampusLink.exe with PyInstaller (onedir).
REM
REM    packaging\build_exe.bat              build only
REM    packaging\build_exe.bat --installer  build then make the installer
REM
REM  Output: dist\CampusLink\CampusLink.exe
REM
REM  NOTE 1: this file must stay ASCII-only.
REM    cmd.exe reads .bat as the OEM codepage (GBK on Chinese Windows).
REM    Chinese text saved as UTF-8 turns into mojibake, and the garbage
REM    used to corrupt the command lines that follow it. Keep it ASCII.
REM
REM  NOTE 2: clean dist before building.
REM    PyInstaller's COLLECT step removes the old output directory itself,
REM    but that is a bulk delete. On machines with endpoint security
REM    software or strict group policy it gets blocked and the build dies
REM    with a confusing error. Cleaning here keeps failures obvious.
REM ===============================================================
setlocal
cd /d "%~dp0\.."

set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] .venv not found.
    echo         Run:  python -m venv .venv
    echo               .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo ==============================================================
echo  CampusLink build
echo ==============================================================
echo.

echo [1/5] Checking dependencies ...
"%PY%" -c "import PySide6, ddddocr, keyring, cv2" 2>nul
if errorlevel 1 (
    echo [ERROR] missing dependencies. Run:
    echo         .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
echo        OK
echo.

echo [2/5] Generating icon ...
"%PY%" "tools\make_icon.py"
if errorlevel 1 echo        [WARN] icon generation failed, using existing app.ico
echo.

echo [3/5] Cleaning previous output ...
if exist "build" rmdir /s /q "build"
if exist "dist\CampusLink" rmdir /s /q "dist\CampusLink"
echo        OK
echo.

echo [4/5] Running PyInstaller (takes a few minutes) ...
"%PY%" -m PyInstaller "packaging\CampusLink.spec" --noconfirm --clean --distpath dist --workpath build
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller failed. Read the messages above.
    pause
    exit /b 1
)
echo.

echo [5/5] Result ...
if not exist "dist\CampusLink\CampusLink.exe" (
    echo [ERROR] dist\CampusLink\CampusLink.exe not found
    pause
    exit /b 1
)
echo        exe : %CD%\dist\CampusLink\CampusLink.exe
echo.

echo ==============================================================
echo  Build finished
echo ==============================================================
echo.
echo   Quick test:  dist\CampusLink\CampusLink.exe
echo   Installer :  packaging\build_installer.bat
echo.

if /i "%~1"=="--installer" (
    call "packaging\build_installer.bat"
    exit /b %errorlevel%
)

pause
