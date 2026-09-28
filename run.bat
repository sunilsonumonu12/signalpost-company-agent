@echo off
echo ============================================
echo   SignalPost Hackathon - RUN START
echo   Companies file: 1000-companies.jsonl
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

REM Check input file exists
if not exist "1000-companies.jsonl" (
    echo ❌ ERROR: 1000-companies.jsonl file nahi mila!
    echo    File ko project root folder me rakho.
    pause
    exit /b 1
)

echo ⏳ Run start ho gaya... Progress har 30 seconds me dikhega.
echo    Final output yaha milega: result\envelopes.jsonl
echo.

REM Run the project with default 1000 companies file
python run.py --organisations 1000-companies.jsonl

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
