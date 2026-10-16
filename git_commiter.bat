@echo off
title Git Add Commit Push

cd /d "%~dp0"

echo.
echo === Git Status ===
git status

echo.
set /p msg=Enter commit message: 

if "%msg%"=="" (
    echo Error: Commit message cannot be empty.
    pause
    exit /b 1
)

echo.
echo === Adding Changes ===
git add .
if errorlevel 1 goto error

echo.
echo === Committing Changes ===
git commit -m "%msg%"
if errorlevel 1 goto error

echo.
echo === Pushing to Origin ===
git push origin
if errorlevel 1 goto error

echo.
echo === Push Successful ===
pause
exit /b 0

:error
echo.
echo ERROR: Git command failed.
pause
exit /b 1