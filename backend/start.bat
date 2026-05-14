@echo off
echo ========================================
echo   书童 - Starting...
echo ========================================
echo.

cd /d %~dp0

:: Check if .env exists
if not exist .env (
    echo [INFO] Creating .env from .env.example...
    copy .env.example .env >nul
    echo [INFO] Please edit .env with your API key and database settings.
)

:: Start backend
echo [INFO] Starting backend server...
start "MyAgent-Backend" cmd /c "pip install -r requirements-dev.txt 2>nul || echo [WARN] pip install skipped && python main.py"

:: Wait for backend
timeout /t 2 >nul

:: Open browser
start http://127.0.0.1:8000

echo [OK] 书童 is running at http://127.0.0.1:8000
echo [TIP] For frontend development: cd frontend ^&^& npm run dev
pause
