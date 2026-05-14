"""Entry point for the Agent Backend.

Usage:
    python main.py              # Start API server only
    python main.py --cli        # Start interactive CLI mode
"""

from __future__ import annotations

import asyncio
import sys
import threading
import webbrowser

import uvicorn
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from app.api.chat import router as chat_router
from app.api.session import router as session_router
from app.api.memory_api import router as memory_router
from app.api.skills_api import router as skills_router
from app.models.database import init_db
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database tables
    try:
        await init_db()
        print(f"[OK] Database initialized")
    except Exception as e:
        print(f"[WARN] Database init failed (retry on first request): {e}")
    yield


app = FastAPI(
    title="书童",
    description="Multi-functional intelligent agent with layered memory system",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow local frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(session_router)
app.include_router(memory_router)
app.include_router(skills_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# Serve built frontend if available
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str = ""):
        # API routes are handled above, this catches everything else
        file_path = FRONTEND_DIST / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIST / "index.html")


def open_browser():
    webbrowser.open("http://127.0.0.1:8000")


def main():
    if "--cli" in sys.argv:
        asyncio.run(run_cli())
    else:
        # Auto-open browser after 1 second
        if "--no-browser" not in sys.argv:
            threading.Timer(1.0, open_browser).start()
        uvicorn.run(
            "main:app",
            host="127.0.0.1",
            port=8000,
            reload=False,
            log_level="info",
        )


async def run_cli():
    """Interactive CLI mode for quick testing."""
    from app.services.agent_service import AgentService

    print("=" * 50)
    print("书童 - Interactive CLI")
    print(f"Model: {settings.llm_model}")
    print("Type 'exit' to quit, 'clear' to reset session")
    print("=" * 50)

    agent_service = AgentService()
    session_id = "cli_session"

    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not question:
            continue
        if question.lower() == "exit":
            break
        if question.lower() == "clear":
            session_id = f"cli_session_{asyncio.get_event_loop().time()}"
            print("[Session cleared]")
            continue

        print()
        collected = []
        try:
            async for chunk in agent_service.stream_chat(question, session_id):
                print(chunk, end="", flush=True)
                collected.append(chunk)
            print()

            # Trigger memory extraction after each turn
            answer = "".join(collected)
            await agent_service.finalize_turn(session_id, question, answer)
        except Exception as e:
            print(f"\n[Error] {e}")


if __name__ == "__main__":
    main()
