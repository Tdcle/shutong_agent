"""Tool base class — migrated from Spring AI ToolCallback pattern."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDef:
    name: str
    description: str
    fn: Callable[..., Any]
    parameters: dict = field(default_factory=dict)  # JSON Schema for parameters


class ToolRegistry:
    """Central tool registry, equivalent to Spring AI's ToolCallbacks."""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, name: str, description: str, fn: Callable, parameters: dict | None = None):
        self._tools[name] = ToolDef(name=name, description=description, fn=fn, parameters=parameters or {})

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def get_all(self) -> list[ToolDef]:
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict]:
        """Convert to OpenAI function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters if t.parameters else {"type": "object", "properties": {}},
                },
            }
            for t in self._tools.values()
        ]


def tool(name: str, description: str, parameters: dict | None = None):
    """Decorator to register a tool in the default registry.

    Usage:
        @tool("search", "Search the web", {"type": "object", "properties": {"q": {"type": "string"}}})
        def search(q: str) -> str: ...
    """
    import inspect

    def decorator(fn: Callable):
        sig = inspect.signature(fn)
        # Auto-generate parameters schema from function signature if not provided
        if parameters is None and name not in _get_params(fn):
            props = {}
            for p_name, p in sig.parameters.items():
                if p_name in ("self", "cls"):
                    continue
                props[p_name] = {"type": "string", "description": p_name}
            fn._tool_params = {"type": "object", "properties": props, "required": list(props.keys())}
        elif parameters:
            fn._tool_params = parameters
        fn._tool_name = name
        fn._tool_description = description
        return fn

    return decorator


def _get_params(fn: Callable) -> dict:
    return getattr(fn, "_tool_params", {})
