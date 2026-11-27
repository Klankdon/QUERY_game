@echo off
if not exist ".venv" (
    python -m venv .venv
)

call .venv\Scripts\activate
pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)
python game.py %*
pause
