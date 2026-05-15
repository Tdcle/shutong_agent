@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0backend"

echo ========================================
echo    ShuTong - One-Click Start
echo ========================================
echo.

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.11+ first.
    pause
    exit /b 1
)

:: Copy .env if missing
if not exist .env (
    if exist .env.example (
        echo [INFO] Creating .env from .env.example...
        copy .env.example .env >nul
    )
    echo [WARN] Please edit backend\.env to set your LLM_API_KEY.
)

:: Install dependencies
echo [INFO] Installing Python dependencies...
pip install -r requirements-dev.txt >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Some packages failed to install. Trying to continue...
)

:: Start backend
echo.
echo [INFO] Starting ShuTong...
echo [INFO] Backend: http://127.0.0.1:8000
echo [INFO] Press Ctrl+C to stop
echo.

python main.py

pause
