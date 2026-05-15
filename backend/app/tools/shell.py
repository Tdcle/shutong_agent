"""Shell execution tool — runs commands inside the session sandbox."""

from __future__ import annotations

from app.tools.base import tool
from app.tools.sandbox import get_sandbox_manager
from app.tools.workspace import resolve


@tool(
    name="execute_shell",
    description="Execute a shell command inside the sandbox. All file changes are tracked and synced back.",
    permission_level="shell",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "working_dir": {"type": "string", "description": "Working directory, defaults to sandbox workspace"},
        },
        "required": ["command"],
    },
)
async def execute_shell(command: str, working_dir: str = ".") -> str:
    sandbox = get_sandbox_manager()
    try:
        result = await sandbox.run_command(command, working_dir)
        return result.to_summary()
    except TimeoutError:
        return "Error: command execution timed out"
    except Exception as exc:
        return f"Command execution failed: {exc}"
