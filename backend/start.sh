#!/bin/bash
echo "========================================"
echo "  书童 - Starting..."
echo "========================================"

cd "$(dirname "$0")"

# Check if .env exists
if [ ! -f .env ]; then
    echo "[INFO] Creating .env from .env.example..."
    cp .env.example .env
    echo "[INFO] Please edit .env with your API key and database settings."
fi

# Install dependencies if needed
pip install -r requirements-dev.txt 2>/dev/null || echo "[WARN] pip install skipped"

# Start backend (opens browser automatically)
echo "[INFO] Starting backend server..."
python main.py &

# Wait for server
sleep 2

echo "[OK] 书童 is running at http://127.0.0.1:8000"
echo "[TIP] For frontend development: cd frontend && npm run dev"
