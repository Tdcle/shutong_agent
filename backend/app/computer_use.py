"""Computer Use module — Shell execution + (optional) Docker sandbox.

Migrated from Java ShellTool2 with added sandboxing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class ShellExecutor:
    """Execute shell commands with optional Docker sandbox isolation."""

    def __init__(self, timeout: int = 120, work_dir: str | None = None):
        self.timeout = timeout
        self.work_dir = work_dir or os.getcwd()

    async def execute(self, command: str, working_dir: str | None = None) -> dict:
        """Execute a shell command and return stdout, stderr, and exit code.

        Returns:
            {"stdout": str, "stderr": str, "exit_code": int, "success": bool}
        """
        cwd = working_dir or self.work_dir
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            return {
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "exit_code": proc.returncode,
                "success": proc.returncode == 0,
            }
        except asyncio.TimeoutError:
            return {"stdout": "", "stderr": f"命令执行超时 ({self.timeout}s)", "exit_code": -1, "success": False}
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1, "success": False}

    async def execute_safe(self, command: str) -> dict:
        """Execute in a temp working directory for isolation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            return await self.execute(command, working_dir=tmpdir)


class DockerSandbox:
    """Docker-based sandbox for isolated code execution.

    Equivalent to Java's ShellTool2 with a Docker wrapper.
    """

    def __init__(self, image: str = "python:3.11-slim", timeout: int = 120):
        self.image = image
        self.timeout = timeout

    async def execute(self, command: str, language: str = "python") -> dict:
        """Execute code in a Docker container.

        For Python: wraps command in `python -c "..."` inside container.
        For Shell: runs command directly with `sh -c "..."`.
        """
        if language == "python":
            # Escape quotes for safe passing
            escaped = command.replace('"', '\\"')
            cmd = f'docker run --rm --network none -i {self.image} python -c "{escaped}"'
        else:
            escaped = command.replace("'", "'\\''")
            cmd = f"docker run --rm --network none -i {self.image} sh -c '{escaped}'"

        executor = ShellExecutor(timeout=self.timeout)
        return await executor.execute(cmd)

    async def execute_file(self, file_path: str) -> dict:
        """Execute a Python script file inside a Docker container."""
        abs_path = str(Path(file_path).resolve())
        mount_dir = str(Path(file_path).parent.resolve())
        cmd = f"docker run --rm --network none -v {mount_dir}:/code -w /code -i {self.image} python /code/{Path(file_path).name}"
        executor = ShellExecutor(timeout=self.timeout)
        return await executor.execute(cmd)

    async def interactive_session(self, work_dir: str | None = None) -> dict:
        """Start an interactive Docker container for multi-step work.

        Suitable for the plan-execute agent that needs to run multiple commands.
        """
        wd = work_dir or os.getcwd()
        cmd = (
            f"docker run -d --rm -v {wd}:/workspace -w /workspace "
            f"{self.image} tail -f /dev/null"
        )
        executor = ShellExecutor(timeout=30)
        result = await executor.execute(cmd)
        container_id = result["stdout"].strip()
        return {"container_id": container_id, "success": result["success"]}

    async def stop_container(self, container_id: str):
        executor = ShellExecutor(timeout=10)
        await executor.execute(f"docker stop {container_id}")
