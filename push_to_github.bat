@echo off
echo ========================================================
echo   Pushing LiveKit Voice Agent to GitHub
echo   Repository: https://github.com/Sargam-max/VOICE_AGENT_-1
echo ========================================================
echo.

cd /d "%~dp0"

echo [*] Checking git status...
git status

echo.
echo [*] Pushing branch 'main' to origin...
git push -u origin main

if %errorlevel% neq 0 (
    echo.
    echo [!] Push failed or required login.
    echo If prompted, please enter your GitHub Personal Access Token (PAT)
    echo or sign in via browser.
) else (
    echo.
    echo [OK] Code successfully pushed to:
    echo      https://github.com/Sargam-max/VOICE_AGENT_-1
)
pause
