@echo off
echo ============================================
echo   SignalPost Hackathon - SETUP
echo   Dependencies install ho rahe hai...
echo ============================================
echo.

REM Check Python installed hai ya nahi
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ ERROR: Python nahi mila!
    echo    Pehle Python 3.10+ install karo: https://www.python.org/downloads/
    echo    Install karte waqt "Add Python to PATH" jarur tick karo.
    pause
    exit /b 1
)

REM Python version check
for /f "tokens=2 delims= " %%a in ('python --version 2^>^&1') do set PY_VER=%%a
echo ✓ Python version: %PY_VER%
echo.

REM Install dependencies
echo [1/2] Dependencies install kar raha hu...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo ❌ Dependencies install fail hue!
    pause
    exit /b 1
)

echo.
echo [2/2] Setup complete! ✓
echo.
echo ============================================
echo   Ab project chalaane ke liye likho:
echo   run.bat
echo ============================================
echo.
pause
