"""Session workspace management and path validation helpers."""

from __future__ import annotations

import contextvars
from pathlib import Path

from app.config import settings
from app.tools.permissions import PathCapability, get_current_permission_broker

_current_workspace: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "session_workspace",
    default=None,
)


def get_session_workspace() -> Path | None:
    try:
        return _current_workspace.get()
    except LookupError:
        return None


def set_session_workspace(path: Path):
    _current_workspace.set(path)


def create_session_workspace(session_id: str) -> Path:
    ws = Path(settings.workspaces_base) / session_id
    ws.mkdir(parents=True, exist_ok=True)
    return ws.resolve()


def resolve(path: str) -> Path:
    ws = get_session_workspace()
    p = Path(path).expanduser()
    if ws is not None:
        ws = ws.resolve()
        candidate = p.resolve() if p.is_absolute() else (ws / p).resolve()
        if candidate != ws and ws not in candidate.parents:
            raise ValueError(f"Path escapes session workspace: {path}")
        return candidate
    return (Path(settings.workspaces_base) / p).expanduser().resolve()


def resolve_readonly(path: str) -> Path:
    try:
        return resolve(path)
    except ValueError:
        return Path(path).expanduser().resolve()


def ensure_within_workspace(path: str) -> Path:
    resolved = resolve(path)
    ws = get_session_workspace()
    if ws is None:
        raise RuntimeError("Session workspace is not set")
    ws = ws.resolve()
    if resolved != ws and ws not in resolved.parents:
        raise ValueError(f"Path escapes session workspace: {path}")
    return resolved


def resolve_with_capability(
    path: str,
    *,
    tool_name: str,
    arg_name: str,
    capability: PathCapability,
) -> tuple[Path, bool]:
    """Resolve a path and allow external targets when approved by capability.

    READ operations on external paths are always allowed without approval,
    since they are non-destructive.
    """
    try:
        return ensure_within_workspace(path), True
    except ValueError:
        if capability == PathCapability.READ:
            return Path(path).expanduser().resolve(), False
        broker = get_current_permission_broker()
        if broker and broker.is_currently_approved(capability, path, tool_name=tool_name, arg_name=arg_name):
            return Path(path).expanduser().resolve(), False
        raise


def rel_to_workspace(path: Path) -> str:
    ws = get_session_workspace()
    if ws is None:
        raise RuntimeError("Session workspace is not set")
    return path.resolve().relative_to(ws.resolve()).as_posix()
