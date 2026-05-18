"""Entry point for the Agent Backend.

Usage:
    python main.py              # Start API server only
    python main.py --cli        # Start interactive CLI mode
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Load env file for libraries that read directly from os.environ
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            _key, _val = _key.strip(), _val.strip()
            if _key and _key not in os.environ:
                os.environ[_key] = _val

# Prevent httpx from routing localhost through system proxy (Windows)
if os.name == "nt":
    no_proxy = os.environ.get("NO_PROXY", "")
    for host in ("localhost", "127.0.0.1"):
        if host not in no_proxy.split(","):
            no_proxy = f"{no_proxy},{host}" if no_proxy else host
    os.environ["NO_PROXY"] = no_proxy
    os.environ["no_proxy"] = no_proxy

import uvicorn

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from app.api.chat import router as chat_router
from app.api.session import router as session_router
from app.api.memory_api import router as memory_router
from app.api.config_api import router as config_router, apply_user_config
from app.api.skills_api import router as skills_router
from app.api.agents_api import router as agents_router
from app.models.database import init_db
from app.config import settings
from app.tools.sandbox import get_sandbox_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database tables
    try:
        await init_db()
        print(f"[OK] Database initialized")
        apply_user_config()
    except Exception as e:
        print(f"[WARN] Database init failed (retry on first request): {e}")

    # Background sandbox cleanup task
    cleanup_task = None
    async def _sandbox_cleanup_loop():
        from app.tools.sandbox import get_sandbox_manager
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            try:
                get_sandbox_manager().close_inactive()
            except Exception:
                pass

    cleanup_task = asyncio.create_task(_sandbox_cleanup_loop())

    try:
        yield
    finally:
        try:
            get_sandbox_manager().close_inactive()
        except Exception:
            pass

    # Shutdown: cancel cleanup task
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


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
app.include_router(config_router)
app.include_router(agents_router)


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


def main():
    if "--cli" in sys.argv:
        asyncio.run(run_cli())
    else:
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
    from app.tools.workspace import create_session_workspace, set_session_workspace
    from app.tools.sandbox import get_sandbox_manager
    import shutil

    session_id = "cli_session"
    workspace_path = create_session_workspace(session_id)
    set_session_workspace(workspace_path)
    print(f"Workspace: {workspace_path}")

    try:
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
                # Clean up old workspace and create new one
                get_sandbox_manager().destroy_for_session(workspace_path)
                shutil.rmtree(workspace_path, ignore_errors=True)
                session_id = f"cli_session_{asyncio.get_running_loop().time()}"
                workspace_path = create_session_workspace(session_id)
                set_session_workspace(workspace_path)
                print(f"[Session cleared, new workspace: {workspace_path}]")
                continue

            print()
            collected = []
            try:
                async for event in agent_service.stream_chat(question, session_id):
                    if isinstance(event, dict):
                        if event.get("type") == "tool_call":
                            print(f"\n  [{event.get('tool')}]", end=" ", flush=True)
                        elif event.get("type") == "tool_result":
                            status = "OK" if event.get("success") else "FAIL"
                            print(f"({status})", end="", flush=True)
                    else:
                        print(event, end="", flush=True)
                        collected.append(event)
                print()

                answer = "".join(collected)
                await agent_service.finalize_turn(session_id, question, answer)
            except Exception as e:
                print(f"\n[Error] {e}")
            finally:
                pass
    finally:
        get_sandbox_manager().destroy_for_session(workspace_path)
        shutil.rmtree(workspace_path, ignore_errors=True)


if __name__ == "__main__":
    main()
