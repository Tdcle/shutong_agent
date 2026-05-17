"""命令与 Python 执行工具：在当前会话沙箱中运行命令或 Python 代码。"""

from __future__ import annotations

import re
from pathlib import Path

from app.tools.base import tool
from app.tools.sandbox import get_sandbox_manager
from app.tools.workspace import get_session_workspace

# Pattern for Windows absolute paths in Python code (e.g. C:\..., D:/...)
_EXTERNAL_PATH_RE = re.compile(r"[A-Za-z]:[\\/](?:[^\s\"'\n\r]|\\[\\])+")


def _normalize_windows_paths(code: str) -> str:
    """Replace backslash Windows paths with forward-slash to prevent Python escape issues.

    In non-raw strings, backslashes like \\P, \\U trigger SyntaxWarnings or
    mojibake with CJK characters. Forward slashes work identically on Windows
    Python and never cause escape-sequence problems.
    """
    def _replace(match):
        raw = match.group(0)
        if raw.count("\\") >= 1:
            return raw.replace("\\", "/")
        return raw
    return _EXTERNAL_PATH_RE.sub(_replace, code)


def _scan_external_paths(code: str) -> list[str]:
    """Find Windows absolute paths in code that fall outside the workspace."""
    ws = get_session_workspace()
    if ws is None:
        return []
    ws = ws.resolve()
    external: list[str] = []
    seen: set[str] = set()
    for match in _EXTERNAL_PATH_RE.finditer(code):
        raw = match.group(0)
        # Normalize backslashes for Path resolution
        normalized = raw.replace("\\", "/")
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            resolved = Path(normalized).expanduser().resolve()
            if resolved != ws and ws not in resolved.parents:
                external.append(raw)
        except Exception:
            pass
    return external


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
            "working_dir": {"type": "string", "description": "工作目录，默认使用沙箱工作区"},
        },
        "required": ["command"],
    },
)
async def execute_bash(command: str, working_dir: str = ".") -> str:
    sandbox = get_sandbox_manager()
    try:
        result = await sandbox.run_command(command, working_dir)
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
            "working_dir": {
                "type": "string",
                "description": "工作目录，默认使用当前沙箱工作区。",
            },
        },
        "required": ["code"],
    },
)
async def execute_python(code: str, working_dir: str = ".") -> str:
    # Normalize backslash Windows paths to forward slashes before anything else.
    # Prevents SyntaxWarning from \\P, \\U etc. and CJK mojibake in non-raw strings.
    code = _normalize_windows_paths(code)

    # Scan code for external paths before execution
    external_paths = _scan_external_paths(code)
    if external_paths:
        examples = "\n".join(f"  - {p}" for p in external_paths[:5])
        if len(external_paths) > 5:
            examples += f"\n  ... and {len(external_paths) - 5} more"
        return (
            f"External path(s) detected in Python code:\n{examples}\n\n"
            f"These paths are outside the session workspace. First use copy_file to stage the "
            f"files into the workspace, then reference the workspace copies in your Python code."
        )

    sandbox = get_sandbox_manager()
    try:
        result = await sandbox.run_python(code, working_dir)
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
