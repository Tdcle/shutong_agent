@echo off
chcp 65001 >nul
cd /d "%~dp0backend"

echo ========================================
echo   书童 - One-Click Start
echo ========================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.11+ first.
    pause
    exit /b 1
)

:: Copy .env if missing
if not exist .env (
    echo [INFO] Creating .env from .env.example...
    copy .env.example .env >nul
    echo [WARN] Please edit backend\.env to set your LLM_API_KEY.
)

:: Install dependencies
echo [INFO] Installing Python dependencies...
pip install -r requirements-dev.txt 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Some packages failed to install. Trying to continue...
)

:: Start backend (opens browser automatically)
echo.
echo [INFO] Starting 书童...
echo [INFO] Backend: http://127.0.0.1:8000
echo [INFO] Press Ctrl+C to stop
echo.

python main.py

pause
