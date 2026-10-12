@echo off
cd /d "%~dp0"
echo ============================================
echo   SignalPost Hackathon - RUN START
if "%*"=="" (
    echo   Companies file: entry-companies.jsonl (run.py default)
) else (
    echo   Arguments: %*
)
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ ERROR: Python nahi mila!
    echo    Pehle setup.bat chalao.
    pause
    exit /b 1
)

echo ⏳ Run start ho gaya... Progress har 30 seconds me dikhega.
echo    Final output yaha milega: result\envelopes.jsonl
echo.

REM Forward all arguments; with no arguments run.py uses its own default input.
python run.py %*

REM Show exit status
if errorlevel 1 (
    echo.
    echo ============================================
    echo   ❌ RUN FAILED (Error Code: %ERRORLEVEL%)
    echo   Details: out\latest-run\ me check karo
    echo ============================================
) else (
    echo.
    echo ============================================
    echo   ✓ RUN COMPLETE!
    echo   Final Output: result\envelopes.jsonl
    echo   Summary: out\latest-run\run-summary.json
    echo ============================================
)
echo.
pause
