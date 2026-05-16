"""Permission broker for tool approvals and session-scoped path capabilities."""

from __future__ import annotations

import asyncio
import contextvars
import enum
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any



class PermissionLevel(enum.Enum):
    READ = "read"
    WRITE = "write"
    DESTROY = "destroy"
    SHELL = "shell"


class PathCapability(enum.Enum):
    READ = "read"
    WRITE = "write"
    MOVE = "move"
    DELETE = "delete"
    EXEC = "exec"


REMEMBERABLE_LEVELS = {PermissionLevel.WRITE, PermissionLevel.DESTROY}


TOOL_PATH_SCOPES: dict[str, tuple[tuple[str, PathCapability], ...]] = {
    "write_file": (("path", PathCapability.WRITE),),
    "edit_file": (("path", PathCapability.WRITE),),
    "move_file": (("destination", PathCapability.MOVE),),
    "move_paths": (("destination", PathCapability.MOVE), ("paths", PathCapability.MOVE)),
    "delete_file": (("path", PathCapability.DELETE),),
    "delete_paths": (("paths", PathCapability.DELETE),),
    "copy_file": (("destination", PathCapability.WRITE),),
    "copy_paths": (("destination", PathCapability.WRITE), ("paths", PathCapability.READ)),
}


@dataclass
class _PendingRequest:
    id: str
    level: PermissionLevel
    tool_name: str
    args: dict[str, Any]
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: bool = False


@dataclass
class ApprovalContext:
    tool_name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class PathRule:
    capability: PathCapability
    path_prefix: str


class PermissionBroker:
    """Manages in-flight approvals and session-scoped capability rules."""

    def __init__(self):
        self._pending: dict[str, _PendingRequest] = {}
        self._path_rules: set[PathRule] = set()
        self._last_approval: ApprovalContext | None = None

    def create_request(self, level: PermissionLevel, tool_name: str, args: dict[str, Any]) -> str:
        req_id = str(uuid.uuid4())[:8]
        self._pending[req_id] = _PendingRequest(
            id=req_id,
            level=level,
            tool_name=tool_name,
            args=args,
        )
        return req_id

    async def wait(self, request_id: str) -> bool:
        req = self._pending.get(request_id)
        if req is None:
            return False
        try:
            await req.event.wait()
            return req.approved
        finally:
            self._pending.pop(request_id, None)

    def respond(self, request_id: str, approved: bool, remember: bool = False) -> bool:
        req = self._pending.get(request_id)
        if req is None:
            return False
        req.approved = approved
        if approved:
            self._last_approval = ApprovalContext(tool_name=req.tool_name, args=dict(req.args))
        if approved and remember and req.level in REMEMBERABLE_LEVELS:
            self._remember_rules(req.tool_name, req.args)
        req.event.set()
        return True

    def current_approval(self) -> ApprovalContext | None:
        return self._last_approval

    def clear_current_approval(self):
        self._last_approval = None

    def should_skip_prompt(self, tool_name: str, args: dict[str, Any] | None = None) -> bool:
        if not args:
            return False
        scopes = list(self._iter_scope_paths(tool_name, args))
        if not scopes:
            return False
        matched = False
        for capability, raw in scopes:
            matched = True
            if not self.has_rule(capability, raw):
                return False
        return matched

    def has_rule(self, capability: PathCapability, path: str) -> bool:
        resolved = Path(path).expanduser().resolve()
        from app.tools.workspace import get_session_workspace

        workspace = get_session_workspace()
        if workspace is not None:
            workspace = workspace.resolve()
            if resolved == workspace or workspace in resolved.parents:
                return True
        for rule in self._path_rules:
            if rule.capability != capability:
                continue
            prefix = Path(rule.path_prefix)
            if resolved == prefix or prefix in resolved.parents:
                return True
        return False

    def is_currently_approved(self, capability: PathCapability, path: str, *, tool_name: str, arg_name: str) -> bool:
        approval = self._last_approval
        resolved = Path(path).expanduser().resolve()
        if approval and approval.tool_name == tool_name:
            approved_value = approval.args.get(arg_name)
            if isinstance(approved_value, str) and Path(approved_value).expanduser().resolve() == resolved:
                return True
        return self.has_rule(capability, path)

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def _remember_rules(self, tool_name: str, args: dict[str, Any]):
        for capability, raw in self._iter_scope_paths(tool_name, args):
            resolved = Path(raw).expanduser().resolve()
            base = resolved if resolved.exists() and resolved.is_dir() else resolved.parent
            self._path_rules.add(PathRule(capability=capability, path_prefix=str(base)))

    def _iter_scope_paths(self, tool_name: str, args: dict[str, Any]):
        for arg_name, capability in TOOL_PATH_SCOPES.get(tool_name, ()):
            raw = args.get(arg_name)
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str):
                        yield capability, item
                continue
            if isinstance(raw, str):
                yield capability, raw


_current_permission_broker: contextvars.ContextVar[PermissionBroker | None] = contextvars.ContextVar(
    "permission_broker",
    default=None,
)


def set_current_permission_broker(broker: PermissionBroker | None):
    _current_permission_broker.set(broker)


def get_current_permission_broker() -> PermissionBroker | None:
    try:
        return _current_permission_broker.get()
    except LookupError:
        return None
