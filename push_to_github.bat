@echo off
setlocal
echo ========================================================
echo   Pushing LiveKit Voice Agent to GitHub
echo   Target: https://github.com/Sargam-max/VOICE_AGENT_-1
echo ========================================================
echo.

cd /d "%~dp0"

set "PATH=%PATH%;C:\Program Files\GitHub CLI;C:\Users\%USERNAME%\AppData\Local\Microsoft\WinGet\Links;C:\Users\%USERNAME%\AppData\Local\Microsoft\WinGet\Packages\Git.MinGit_Microsoft.Winget.Source_8wekyb3d8bbwe\cmd"

echo [*] Checking GitHub login status...
gh auth status >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo [!] You are not currently logged in to GitHub on this PC.
    echo [*] Opening browser to authenticate with GitHub...
    echo.
    gh auth login --hostname github.com --git-protocol https --web
    if %errorlevel% neq 0 (
        echo [!] Authentication was not completed.
        echo If you have a Personal Access Token (PAT), you can run:
        echo   git push https://YOUR_TOKEN@github.com/Sargam-max/VOICE_AGENT_-1.git main
        pause
        exit /b 1
    )
)

echo.
echo [*] Configuring Git credentials helper...
gh auth setup-git

echo.
echo [*] Pushing branch 'main' to https://github.com/Sargam-max/VOICE_AGENT_-1...
git push -u origin main

if %errorlevel% neq 0 (
    echo.
    echo [!] Push encountered an error. Please review the output above.
) else (
    echo.
    echo ========================================================
    echo  [SUCCESS] Code is now live on GitHub:
    echo  https://github.com/Sargam-max/VOICE_AGENT_-1
    echo ========================================================
)
echo.
pause
