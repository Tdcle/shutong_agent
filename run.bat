@echo off
chcp 65001 >nul 2>&1

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

:: Check Node.js
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not found. Please install Node.js first.
    pause
    exit /b 1
)

:: ---- Backend ----
cd /d "%~dp0backend"

if not exist .env (
    if exist .env.example (
        echo [INFO] Creating .env from .env.example...
        copy .env.example .env >nul
    )
    echo [WARN] Please edit backend\.env to set your LLM_API_KEY.
)

echo [INFO] Installing Python dependencies...
pip install -r requirements-dev.txt >nul 2>&1

echo [INFO] Starting backend on http://127.0.0.1:8000 ...
start "ShuTong-Backend" /MIN python main.py

:: Wait for backend
echo [INFO] Waiting for backend...
:wait_backend
timeout /t 2 /nobreak >nul
curl -s http://127.0.0.1:8000/api/health >nul 2>&1
if %errorlevel% neq 0 goto wait_backend

:: ---- Frontend ----
cd /d "%~dp0frontend"

if not exist node_modules (
    echo [INFO] Installing frontend dependencies...
    call npm install
)

echo [INFO] Starting frontend on http://localhost:5173 ...
start "ShuTong-Frontend" /MIN npx vite --host

:: Wait for frontend
echo [INFO] Waiting for frontend...
:wait_frontend
timeout /t 2 /nobreak >nul
curl -s http://localhost:5173 >nul 2>&1
if %errorlevel% neq 0 goto wait_frontend

:: ---- Open browser ----
start http://localhost:5173

echo.
echo ========================================
echo    Backend:  http://127.0.0.1:8000
echo    Frontend: http://localhost:5173
echo.
echo    Press any key to stop all services...
echo ========================================
pause >nul

:: ---- Cleanup ----
echo [INFO] Stopping backend...
taskkill /FI "WINDOWTITLE eq ShuTong-Backend" /F >nul 2>&1
echo [INFO] Stopping frontend...
taskkill /FI "WINDOWTITLE eq ShuTong-Frontend" /F >nul 2>&1
echo [INFO] All services stopped.
pause
