@echo off
setlocal EnableExtensions
title Gemini Repo Launcher

REM Select a ZIP repository and an instruction Markdown file, extract the ZIP,
REM then open Gemini Desktop and submit a prompt containing the Markdown text
REM and the extracted repository path. This does NOT grant Gemini local-file
REM access or guarantee that Gemini can edit the repository.

set "WORKROOT=%USERPROFILE%\GeminiRepoWork"
if not exist "%WORKROOT%" mkdir "%WORKROOT%"

echo.
echo ==========================================
echo   Gemini Desktop - Repo Instruction Runner
echo ==========================================
echo.

REM Pick repository ZIP
for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.OpenFileDialog; $d.Title='Select the compressed repository ZIP'; $d.Filter='ZIP files (*.zip)|*.zip'; if($d.ShowDialog() -eq 'OK'){[Console]::WriteLine($d.FileName)}"`) do set "ZIPFILE=%%I"
if not defined ZIPFILE (
  echo No ZIP selected. Exiting.
  pause
  exit /b 1
)

REM Pick Markdown instruction file
for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.OpenFileDialog; $d.Title='Select the Markdown instruction file'; $d.Filter='Markdown files (*.md)|*.md|All files (*.*)|*.*'; if($d.ShowDialog() -eq 'OK'){[Console]::WriteLine($d.FileName)}"`) do set "MDFILE=%%I"
if not defined MDFILE (
  echo No Markdown file selected. Exiting.
  pause
  exit /b 1
)

for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "[IO.Path]::GetFileNameWithoutExtension($env:ZIPFILE) -replace '[^a-zA-Z0-9._-]','_'"`) do set "REPONAME=%%I"
set "REPODIR=%WORKROOT%\%REPONAME%"

echo.
echo Extracting repository to:
echo "%REPODIR%"
if exist "%REPODIR%" (
  echo Existing extraction found. It will be reused and overwritten files may remain.
) else (
  mkdir "%REPODIR%"
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath $env:ZIPFILE -DestinationPath $env:REPODIR -Force"
if errorlevel 1 (
  echo Failed to extract ZIP.
  pause
  exit /b 1
)

REM Build a prompt containing the MD instructions and the local path.
set "PROMPTFILE=%WORKROOT%\gemini_prompt.txt"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$md = Get-Content -LiteralPath $env:MDFILE -Raw; " ^
  "$repo = $env:REPODIR; " ^
  "$nl = [Environment]::NewLine; " ^
  "$prompt = 'You are being given instructions from a Markdown file and a local repository location.' + $nl + $nl + 'LOCAL REPOSITORY PATH:' + $nl + $repo + $nl + $nl + 'IMPORTANT: The repository is on my Windows computer. Do not claim you can read or modify local files unless the app explicitly provides that access. First state whether you can access this path. If you cannot, tell me exactly what file/folder upload or supported tool is needed. Follow the instructions below once the repository is accessible.' + $nl + $nl + '===== BEGIN MARKDOWN INSTRUCTIONS =====' + $nl + $md + $nl + '===== END MARKDOWN INSTRUCTIONS ====='; " ^
  "Set-Content -LiteralPath $env:PROMPTFILE -Value $prompt -Encoding UTF8; " ^
  "Set-Clipboard -Value $prompt"
if errorlevel 1 (
  echo Failed to prepare the prompt.
  pause
  exit /b 1
)

REM Try to launch Gemini Desktop. If the app is already running, Windows may focus it.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a = Get-StartApps | Where-Object { $_.Name -match 'Gemini' } | Select-Object -First 1; if ($a) { Start-Process explorer.exe ('shell:AppsFolder\' + $a.AppID) } else { Write-Host 'Gemini app not found in Start menu. Open it manually, then continue.' }"
timeout /t 3 /nobreak >nul

REM Alt+Space is Google's documented Windows shortcut for Gemini. Paste prompt and submit.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "Start-Sleep -Milliseconds 1200; " ^
  "$ws.SendKeys('% '); Start-Sleep -Milliseconds 900; " ^
  "$ws.SendKeys('^v'); Start-Sleep -Milliseconds 500; " ^
  "$ws.SendKeys('{ENTER}')"

echo.
echo Prompt copied and submitted to Gemini Desktop (if the shortcut/UI accepted it).
echo Repository extracted to: "%REPODIR%"
echo Prompt saved to: "%PROMPTFILE%"
echo.
echo IMPORTANT:
echo Gemini Desktop may not be able to access or edit this local folder.
echo This BAT does not bypass that limitation or verify that the prompt was sent.
echo Check Gemini's response before assuming any repository work happened.
echo.
pause
