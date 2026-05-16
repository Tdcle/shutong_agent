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

    def to_openai_tools(self) -> list[dict]:
        """Convert tools to OpenAI function definitions."""
        tools: list[dict] = []
        for tool_def in self._tools.values():
            params = dict(tool_def.parameters) if tool_def.parameters else {"type": "object", "properties": {}}
            props = dict(params.get("properties", {}))
            required = list(params.get("required", []))
            props["purpose"] = {
                "type": "string",
                "description": "请简要说明为什么调用这个工具，以及希望通过它完成什么。",
            }
            if "purpose" not in required:
                required.append("purpose")
            params["properties"] = props
            params["required"] = required
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_def.name,
                        "description": tool_def.description,
                        "parameters": params,
                    },
                }
            )
        return tools


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
