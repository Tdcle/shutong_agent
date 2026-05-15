"""Permission system for tool execution safety.

Permission levels:
  READ    — auto-approved (read_file, grep, glob, list_files)
  WRITE   — prompt user once, then optional "remember" (write_file, edit_file, move_file)
  DESTROY — always prompt (delete_file)
  SHELL   — always prompt (execute_shell)

Session allowlist:
  When the user checks "remember" in the permission dialog, that tool name is
  added to a session-scoped allowlist and auto-approved for the rest of the session.

Flow:
  1. Agent checks broker.is_allowlisted(tool_name) → if yes, skip prompt
  2. Agent calls broker.create_request() → gets request_id
  3. Agent yields {"type": "permission_request", "request_id": ..., ...} to SSE stream
  4. Agent calls broker.wait(request_id) → blocks until user responds
  5. User clicks → frontend calls POST /api/chat/permission-response
  6. API calls broker.respond(request_id, approved, remember) → unblocks step 4
  7. If remember=True, tool_name is added to allowlist for the session
"""

from __future__ import annotations

import asyncio
import enum
import uuid
from dataclasses import dataclass, field
from typing import Any


class PermissionLevel(enum.Enum):
    READ = "read"
    WRITE = "write"
    DESTROY = "destroy"
    SHELL = "shell"

# Tools that support "remember this session" — destroy and shell should always prompt
REMEMBERABLE_LEVELS = {PermissionLevel.WRITE}


@dataclass
class _PendingRequest:
    id: str
    level: PermissionLevel
    tool_name: str
    args: dict
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: bool = False


class PermissionBroker:
    """Manages in-flight permission requests and session-scoped allowlist."""

    def __init__(self, auto_approve_read: bool = True):
        self._pending: dict[str, _PendingRequest] = {}
        self.auto_approve_read = auto_approve_read
        self._allowlist: set[str] = set()  # tool names trusted for this session

    # ── Allowlist ──────────────────────────────────────────────────────

    def is_allowlisted(self, tool_name: str) -> bool:
        """Check whether a tool has been pre-approved for this session."""
        return tool_name in self._allowlist

    # ── Request lifecycle ──────────────────────────────────────────────

    def create_request(self, level: PermissionLevel, tool_name: str, args: dict) -> str:
        """Create a permission request (non-blocking). Returns request_id."""
        req_id = str(uuid.uuid4())[:8]
        self._pending[req_id] = _PendingRequest(
            id=req_id, level=level, tool_name=tool_name, args=args,
        )
        return req_id

    async def wait(self, request_id: str) -> bool:
        """Block until the user responds to this request. Returns True if approved."""
        req = self._pending.get(request_id)
        if req is None:
            return False
        try:
            await req.event.wait()
            return req.approved
        finally:
            self._pending.pop(request_id, None)

    def respond(self, request_id: str, approved: bool, remember: bool = False) -> bool:
        """Resolve a pending permission request. Called from API endpoint.

        When *remember* is True, the tool name is added to the session allowlist
        so future uses skip the prompt.
        """
        req = self._pending.get(request_id)
        if req is None:
            return False
        req.approved = approved
        if approved and remember and req.level in REMEMBERABLE_LEVELS:
            self._allowlist.add(req.tool_name)
        req.event.set()
        return True

    @property
    def has_pending(self) -> bool:
        return len(self._pending) > 0
