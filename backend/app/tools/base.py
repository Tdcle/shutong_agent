"""Tool registration primitives."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDef:
    name: str
    description: str
    fn: Callable[..., Any]
    parameters: dict = field(default_factory=dict)
    permission_level: str = "read"
    visible: bool = True


class ToolRegistry:
    """Central tool registry."""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(
        self,
        name: str,
        description: str,
        fn: Callable,
        parameters: dict | None = None,
        permission_level: str = "read",
        visible: bool = True,
    ):
        self._tools[name] = ToolDef(
            name=name,
            description=description,
            fn=fn,
            parameters=parameters or {},
            permission_level=permission_level,
            visible=visible,
        )

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def get_all(self) -> list[ToolDef]:
        return list(self._tools.values())


def tool(
    name: str,
    description: str,
    parameters: dict | None = None,
    permission_level: str = "read",
    visible: bool = True,
):
    """Decorator to mark a function as a tool."""
    import inspect

    def decorator(fn: Callable):
        sig = inspect.signature(fn)
        if parameters is None and name not in _get_params(fn):
            props = {}
            for param_name, param in sig.parameters.items():
                if param_name in ("self", "cls"):
                    continue
                props[param_name] = {"type": "string", "description": param_name}
            fn._tool_params = {"type": "object", "properties": props, "required": list(props.keys())}
        elif parameters:
            fn._tool_params = parameters
        fn._tool_name = name
        fn._tool_description = description
        fn._tool_permission_level = permission_level
        fn._tool_visible = visible
        return fn

    return decorator


def _get_params(fn: Callable) -> dict:
    return getattr(fn, "_tool_params", {})
