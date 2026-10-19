@echo off
setlocal
cd /d "%~dp0\.."

set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" goto no_venv

"%PY%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 goto no_pyinstaller

echo [1/3] Cleaning previous output ...
if exist "build" rmdir /s /q "build"
if exist "dist\CampusNetAutoLogin" rmdir /s /q "dist\CampusNetAutoLogin"

echo [2/3] Running PyInstaller ...
"%PY%" -m PyInstaller "packaging\CampusNetAutoLogin.spec" --noconfirm --clean --distpath dist --workpath build
if errorlevel 1 goto failed

echo [3/3] Checking result ...
if not exist "dist\CampusNetAutoLogin\CampusNetAutoLogin.exe" goto missing_exe

echo.
echo Build OK: %CD%\dist\CampusNetAutoLogin\CampusNetAutoLogin.exe
echo Installer: packaging\build_installer.bat
pause
exit /b 0

:no_venv
echo [ERROR] .venv not found. Run setup.bat first.
pause
exit /b 1

:no_pyinstaller
echo [ERROR] PyInstaller is not installed in .venv.
echo         Run: .venv\Scripts\python.exe -m pip install pyinstaller
pause
exit /b 1

:missing_exe
echo [ERROR] dist\CampusNetAutoLogin\CampusNetAutoLogin.exe not found.
pause
exit /b 1

:failed
echo [ERROR] Build failed. Read the messages above.
pause
exit /b 1
