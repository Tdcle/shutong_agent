"""命令与 Python 执行工具：在当前会话沙箱中运行命令或 Python 代码。"""

from __future__ import annotations

from app.tools.base import tool
from app.tools.sandbox import get_sandbox_manager


@tool(
    name="execute_bash",
    description=(
        "在沙箱中执行 bash 命令。使用 Git Bash（Windows 上最常见的 bash 环境），"
        "支持标准 bash 语法（管道、重定向、变量、条件判断等）。"
        "所有文件改动都会被跟踪并同步回工作区。"
    ),
    permission_level="shell",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 bash 命令"},
        },
        "required": ["command"],
    },
)
async def execute_bash(command: str) -> str:
    sandbox = get_sandbox_manager()
    try:
        result = await sandbox.run_command(command)
        summary = result.to_summary()
        if result.success:
            return summary
        return f"Bash execution failed:\n{summary}"
    except TimeoutError:
        return "Error: bash execution timed out"
    except Exception as exc:
        return f"Bash execution failed: {exc}"


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
        },
        "required": ["code"],
    },
)
async def execute_python(code: str) -> str:
    sandbox = get_sandbox_manager()
    try:
        result = await sandbox.run_python(code)
        summary = result.to_summary()
        if result.success:
            return summary
        # Surface common error patterns with actionable hints
        if "ModuleNotFoundError" in result.stderr or "No module named" in result.stderr:
            summary += (
                "\n\n[Missing module] The agent Python environment has limited packages. "
                "If a required module is missing, report to the user that this capability is "
                "not available. Do NOT attempt pip install — package installation is disabled."
            )
        return f"Python execution failed:\n{summary}"
    except TimeoutError:
        return "Error: python execution timed out"
    except Exception as exc:
        return f"Python execution failed: {exc}"
