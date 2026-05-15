"""Session workspace management and workspace-bound path validation.

Each chat session gets its own isolated working directory under workspaces_base.
Tools resolve relative paths against the current session workspace. Absolute paths
must still remain within that workspace boundary.
"""

from __future__ import annotations

import contextvars
from pathlib import Path

from app.config import settings

# Context variable — set per-session at the start of each chat stream
_current_workspace: contextvars.ContextVar[Path] = contextvars.ContextVar(
    "session_workspace", default=None
)


def get_session_workspace() -> Path | None:
    """Return the current session's workspace dir, or None if not set."""
    try:
        ws = _current_workspace.get()
        return ws
    except LookupError:
        return None


def set_session_workspace(path: Path):
    """Set the workspace for the current async context."""
    _current_workspace.set(path)


def resolve(path: str) -> Path:
    """Resolve a tool path against the session workspace.

    - Relative paths → relative to session workspace
    - Absolute paths → used as-is (expanded)
    - If no session workspace set → relative to workspaces_base
    """
    ws = get_session_workspace()
    p = Path(path).expanduser()
    if ws is not None:
        ws = ws.resolve()
        if p.is_absolute():
            candidate = p.resolve()
        else:
            candidate = (ws / p).resolve()
        if candidate != ws and ws not in candidate.parents:
            raise ValueError(f"Path escapes session workspace: {path}")
        return candidate

    # Fallback: use workspaces_base
    return (Path(settings.workspaces_base) / p).expanduser().resolve()


def create_session_workspace(session_id: str) -> Path:
    """Create and return a workspace directory for a session."""
    ws = Path(settings.workspaces_base) / session_id
    ws.mkdir(parents=True, exist_ok=True)
    return ws.resolve()


def ensure_within_workspace(path: str) -> Path:
    """Resolve a path and verify it stays inside the current session workspace."""
    resolved = resolve(path)
    ws = get_session_workspace()
    if ws is None:
        raise RuntimeError("Session workspace is not set")
    ws = ws.resolve()
    if resolved != ws and ws not in resolved.parents:
        raise ValueError(f"Path escapes session workspace: {path}")
    return resolved


def rel_to_workspace(path: Path) -> str:
    """Return a workspace-relative POSIX path for a validated session path."""
    ws = get_session_workspace()
    if ws is None:
        raise RuntimeError("Session workspace is not set")
    return path.resolve().relative_to(ws.resolve()).as_posix()
