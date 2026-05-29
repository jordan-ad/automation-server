@echo off
cd /d "%~dp0"
python app.py
if errorlevel 1 (
    echo.
    echo ───────────────────────────────────────────────
    echo The app crashed or could not start.
    echo Common causes:
    echo   - Python is not installed
    echo   - Required packages are missing
    echo     ^(run: pip install customtkinter tkinterdnd2^)
    echo   - ffmpeg is not installed
    echo     ^(run: winget install Gyan.FFmpeg^)
    echo ───────────────────────────────────────────────
    pause
)
