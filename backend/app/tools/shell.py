"""Shell execution tool — migrated from Java ShellTool2."""

from __future__ import annotations

import asyncio
import shlex

from app.config import settings
from app.tools.base import tool


@tool(
    name="execute_shell",
    description="在终端中执行 Shell 命令并返回结果",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 Shell 命令"},
            "working_dir": {"type": "string", "description": "工作目录，可选"},
        },
        "required": ["command"],
    },
)
async def execute_shell(command: str, working_dir: str = ".") -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=settings.shell_timeout_seconds,
        )
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]\n{err}")
        return "\n".join(parts) if parts else f"命令执行完成，退出码: {proc.returncode}"
    except asyncio.TimeoutError:
        return f"错误: 命令执行超时 ({settings.shell_timeout_seconds}秒)"
    except Exception as e:
        return f"执行命令失败: {e}"
