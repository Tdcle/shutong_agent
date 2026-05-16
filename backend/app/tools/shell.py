"""命令与 Python 执行工具：在当前会话沙箱中运行命令或 Python 代码。"""

from __future__ import annotations

from app.tools.base import tool
from app.tools.sandbox import get_sandbox_manager


@tool(
    name="execute_shell",
    description="在沙箱中执行 shell 命令。所有文件改动都会被跟踪，并在安全检查后同步回工作区。",
    permission_level="shell",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "working_dir": {"type": "string", "description": "工作目录，默认使用沙箱工作区"},
        },
        "required": ["command"],
    },
)
async def execute_shell(command: str, working_dir: str = ".") -> str:
    sandbox = get_sandbox_manager()
    try:
        result = await sandbox.run_command(command, working_dir)
        summary = result.to_summary()
        if result.success:
            return summary
        return f"Command execution failed:\n{summary}"
    except TimeoutError:
        return "Error: command execution timed out"
    except Exception as exc:
        return f"Command execution failed: {exc}"


@tool(
    name="execute_python",
    description=(
        "在沙箱中直接执行 Python 代码。适合循环、批量生成文件、文本处理、JSON/CSV 处理等任务。"
        "不要把 Python 代码再包进 shell 命令里；如果要运行 Python，请优先使用这个工具。"
    ),
    permission_level="shell",
    parameters={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "要执行的 Python 代码，支持多行。请直接写合法的 Python 脚本内容，"
                    "不要传 python -c 命令，也不要加 powershell/cmd 包装。"
                ),
            },
            "working_dir": {
                "type": "string",
                "description": "工作目录，默认使用当前沙箱工作区。",
            },
        },
        "required": ["code"],
    },
)
async def execute_python(code: str, working_dir: str = ".") -> str:
    sandbox = get_sandbox_manager()
    try:
        result = await sandbox.run_python(code, working_dir)
        summary = result.to_summary()
        if result.success:
            return summary
        return f"Python execution failed:\n{summary}"
    except TimeoutError:
        return "Error: python execution timed out"
    except Exception as exc:
        return f"Python execution failed: {exc}"
