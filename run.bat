@echo off
cd /d "%~dp0"
echo Starting ContentProtector...
echo.

where python >nul 2>&1
if %errorlevel% == 0 (
    python app.py
) else (
    where py >nul 2>&1
    if %errorlevel% == 0 (
        py app.py
    ) else (
        echo ERROR: Python not found. Make sure Python is installed and on your PATH.
        pause
        exit /b 1
    )
)
pause
