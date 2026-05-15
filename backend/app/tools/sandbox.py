"""Session-scoped sandbox manager for isolated edits and command execution.

This is the minimal viable implementation described in backend/docs/sandbox-design.md:

- Lazy session sandbox creation
- A single active sandbox per session workspace
- Writable output workspace copied from the host session workspace
- Diff/artifact generation when syncing changes back
- Conflict detection based on last-synced host hashes
"""

from __future__ import annotations

import asyncio
import ctypes
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.tools.workspace import ensure_within_workspace, get_session_workspace, rel_to_workspace

try:
    import pywintypes
    import win32api
    import win32con
    import win32event
    import win32file
    import win32process
    import win32security
except ImportError:  # pragma: no cover - optional on non-Windows hosts
    pywintypes = None
    win32api = None
    win32con = None
    win32event = None
    win32file = None
    win32process = None
    win32security = None

SANDBOX_DIRNAME = ".sandbox"
OUTPUT_WORKSPACE_REL = Path(SANDBOX_DIRNAME) / "output" / "workspace"
ARTIFACTS_REL = Path(SANDBOX_DIRNAME) / "output" / "artifacts"
MAX_DIFF_PREVIEW = 4000
TEXT_EXTENSIONS = {
    ".bat",
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".env",
    ".gitignore",
    ".go",
    ".h",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}

IS_WINDOWS = os.name == "nt"
CREATE_SUSPENDED_FLAG = 0x00000004
CREATE_NEW_PROCESS_GROUP_FLAG = 0x00000200
CREATE_NO_WINDOW_FLAG = 0x08000000
CREATE_UNICODE_ENVIRONMENT_FLAG = 0x00000400


@dataclass
class ArtifactItem:
    path: str
    kind: str
    size: int
    sha256: str
    target_hint: str


@dataclass
class SyncResult:
    profile: str
    change_set: dict[str, list[str]]
    diff_text: str = ""
    artifacts: list[ArtifactItem] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return any(self.change_set.values())

    def to_summary(self) -> str:
        if self.conflicts:
            return "Sandbox sync failed due to conflicts:\n" + "\n".join(f"- {item}" for item in self.conflicts)

        added = len(self.change_set["added_text_files"]) + len(self.change_set["added_binary_files"])
        modified = len(self.change_set["modified_text_files"]) + len(self.change_set["modified_binary_files"])
        deleted = len(self.change_set["deleted_text_files"]) + len(self.change_set["deleted_binary_files"])
        lines = [f"Sandbox profile: {self.profile}"]
        if not self.has_changes:
            lines.append("No file changes detected.")
        else:
            lines.append(f"Changes applied: {added} added, {modified} modified, {deleted} deleted.")
            if self.diff_text:
                preview = self.diff_text
                if len(preview) > MAX_DIFF_PREVIEW:
                    preview = preview[:MAX_DIFF_PREVIEW] + "\n... diff truncated ..."
                lines.append("[diff]")
                lines.append(preview)
            if self.artifacts:
                lines.append("[artifacts]")
                for item in self.artifacts:
                    lines.append(f"- {item.path} ({item.kind}, {item.size} bytes)")
        return "\n".join(lines)


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int
    success: bool
    sync_result: SyncResult

    def to_summary(self) -> str:
        parts: list[str] = [f"Exit code: {self.exit_code}"]
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append("[stderr]")
            parts.append(self.stderr.strip())
        sync_summary = self.sync_result.to_summary()
        if sync_summary:
            parts.append(sync_summary)
        return "\n".join(parts)


@dataclass
class SandboxState:
    session_root: Path
    profile: str
    output_workspace: Path
    artifacts_dir: Path
    initialized: bool = False
    last_synced_hashes: dict[str, str | None] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)


class SandboxConflictError(RuntimeError):
    pass


class SessionSandboxManager:
    """Tracks one active sandbox per session workspace."""

    def __init__(self):
        self._states: dict[str, SandboxState] = {}

    def ensure(self, profile: str) -> SandboxState:
        self.close_inactive()
        session_root = self._require_session_root()
        key = str(session_root)
        state = self._states.get(key)
        if state is None:
            state = SandboxState(
                session_root=session_root,
                profile=profile,
                output_workspace=session_root / OUTPUT_WORKSPACE_REL,
                artifacts_dir=session_root / ARTIFACTS_REL,
            )
            state.output_workspace.mkdir(parents=True, exist_ok=True)
            state.artifacts_dir.mkdir(parents=True, exist_ok=True)
            self._states[key] = state
        elif self._profile_rank(profile) > self._profile_rank(state.profile):
            state.profile = profile

        state.last_active_at = time.time()
        if not state.initialized:
            self._materialize_output_workspace(state)
        return state

    def writable_path(self, path: str, profile: str = "edit") -> tuple[SandboxState, Path, Path, str]:
        self.close_inactive()
        state = self.ensure(profile)
        host_path = ensure_within_workspace(path)
        rel_path = rel_to_workspace(host_path)
        output_path = state.output_workspace / rel_path
        return state, host_path, output_path, rel_path

    async def run_command(self, command: str, working_dir: str = ".", timeout_seconds: int | None = None) -> CommandResult:
        state = self.ensure("exec")
        blocked_reason = self._validate_command(command)
        if blocked_reason:
            return CommandResult(
                stdout="",
                stderr=blocked_reason,
                exit_code=-1,
                success=False,
                sync_result=SyncResult(
                    profile=state.profile,
                    change_set={
                        "added_text_files": [],
                        "added_binary_files": [],
                        "modified_text_files": [],
                        "modified_binary_files": [],
                        "deleted_text_files": [],
                        "deleted_binary_files": [],
                    },
                ),
            )
        try:
            output_cwd = self._resolve_output_working_dir(state, working_dir)
        except ValueError as exc:
            return CommandResult(
                stdout="", stderr=str(exc), exit_code=-1, success=False,
                    sync_result=SyncResult(profile=state.profile, change_set={
                        "added_text_files": [],
                        "added_binary_files": [],
                        "modified_text_files": [],
                        "modified_binary_files": [],
                        "deleted_text_files": [],
                        "deleted_binary_files": [],
                    }),
                )
        output_cwd.mkdir(parents=True, exist_ok=True)

        # Build a restricted environment: redirect HOME/TEMP to sandbox paths,
        # set SANDBOX_WORKSPACE so the command knows where it should operate.
        # Note: this is file-level isolation (MVP). Full process isolation
        # requires a container runtime (see docs/sandbox-design.md).
        sandbox_tmp = state.output_workspace.parent / "tmp"
        sandbox_tmp.mkdir(parents=True, exist_ok=True)
        sandbox_home = state.output_workspace

        restricted_env = {
            **os.environ,
            "HOME": str(sandbox_home),
            "USERPROFILE": str(sandbox_home),
            "TEMP": str(sandbox_tmp),
            "TMP": str(sandbox_tmp),
            "SANDBOX_WORKSPACE": str(state.output_workspace),
            "SANDBOX_PROFILE": state.profile,
        }
        if settings.shell_block_network:
            restricted_env.update(
                {
                    "HTTP_PROXY": settings.shell_network_proxy_url,
                    "HTTPS_PROXY": settings.shell_network_proxy_url,
                    "ALL_PROXY": settings.shell_network_proxy_url,
                    "NO_PROXY": "localhost,127.0.0.1",
                    "http_proxy": settings.shell_network_proxy_url,
                    "https_proxy": settings.shell_network_proxy_url,
                    "all_proxy": settings.shell_network_proxy_url,
                    "PIP_NO_INDEX": "1",
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                    "UV_OFFLINE": "1",
                    "NPM_CONFIG_OFFLINE": "true",
                    "YARN_ENABLE_NETWORK": "0",
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_ASKPASS": "",
                }
            )

        timeout = timeout_seconds or settings.shell_timeout_seconds
        stdout, stderr, returncode = await asyncio.to_thread(
            self._run_command_sync,
            command,
            output_cwd,
            restricted_env,
            timeout,
        )

        sync_result = self.sync_back(state)
        return CommandResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=returncode,
            success=returncode == 0 and not sync_result.conflicts,
            sync_result=sync_result,
        )

    def sync_back(self, state: SandboxState) -> SyncResult:
        host_snapshot = self._scan_workspace(state.session_root)
        output_snapshot = self._scan_workspace(state.output_workspace, exclude_sandbox=False)
        change_set = {
            "added_text_files": [],
            "modified_text_files": [],
            "deleted_text_files": [],
            "added_binary_files": [],
            "modified_binary_files": [],
            "deleted_binary_files": [],
        }
        artifacts: list[ArtifactItem] = []
        diff_chunks: list[str] = []

        host_paths = set(host_snapshot)
        output_paths = set(output_snapshot)
        all_paths = sorted(host_paths | output_paths)
        changed_paths: list[str] = []

        for rel_path in all_paths:
            host_meta = host_snapshot.get(rel_path)
            out_meta = output_snapshot.get(rel_path)
            host_hash = host_meta["sha256"] if host_meta else None
            out_hash = out_meta["sha256"] if out_meta else None
            if host_hash == out_hash:
                continue
            changed_paths.append(rel_path)

        conflicts: list[str] = []
        for rel_path in changed_paths:
            expected_hash = state.last_synced_hashes.get(rel_path)
            current_hash = host_snapshot.get(rel_path, {}).get("sha256")
            if expected_hash != current_hash:
                conflicts.append(rel_path)

        if conflicts:
            return SyncResult(profile=state.profile, change_set=change_set, conflicts=conflicts)

        for rel_path in changed_paths:
            host_meta = host_snapshot.get(rel_path)
            out_meta = output_snapshot.get(rel_path)
            host_exists = host_meta is not None
            out_exists = out_meta is not None
            is_text = self._is_text_path(rel_path)

            if not host_exists and out_exists:
                if is_text:
                    change_set["added_text_files"].append(rel_path)
                    diff_chunks.append(self._build_unified_diff(rel_path, "", self._read_text(out_meta["path"])))
                else:
                    change_set["added_binary_files"].append(rel_path)
                    artifacts.append(self._artifact_for(rel_path, out_meta["path"]))
            elif host_exists and not out_exists:
                if is_text:
                    change_set["deleted_text_files"].append(rel_path)
                    diff_chunks.append(self._build_unified_diff(rel_path, self._read_text(host_meta["path"]), ""))
                else:
                    change_set["deleted_binary_files"].append(rel_path)
            else:
                if is_text:
                    change_set["modified_text_files"].append(rel_path)
                    diff_chunks.append(
                        self._build_unified_diff(
                            rel_path,
                            self._read_text(host_meta["path"]),
                            self._read_text(out_meta["path"]),
                        )
                    )
                else:
                    change_set["modified_binary_files"].append(rel_path)
                    artifacts.append(self._artifact_for(rel_path, out_meta["path"]))

            self._apply_single_change(state.session_root, state.output_workspace, rel_path)

        state.last_synced_hashes = self._snapshot_hashes(self._scan_workspace(state.session_root))
        state.last_active_at = time.time()
        return SyncResult(
            profile=state.profile,
            change_set=change_set,
            diff_text="".join(diff_chunks).strip(),
            artifacts=artifacts,
        )

    def destroy_for_session(self, session_workspace: Path):
        """Destroy the sandbox for a specific session workspace."""
        key = str(session_workspace.resolve())
        self._destroy_state(key)

    def close_inactive(self):
        now = time.time()
        max_idle = settings.sandbox_idle_ttl_seconds
        max_age = settings.sandbox_max_lifetime_seconds
        to_delete = []
        for key, state in self._states.items():
            idle = now - state.last_active_at
            age = now - state.created_at
            if idle > max_idle or age > max_age:
                to_delete.append(key)
        for key in to_delete:
            self._destroy_state(key)

    def _destroy_state(self, key: str):
        state = self._states.pop(key, None)
        if state and state.output_workspace.parent.parent.exists():
            target = state.output_workspace.parent.parent
            tmp_dir = target / "output" / "tmp"
            if tmp_dir.exists():
                for child in tmp_dir.glob("*"):
                    try:
                        child.unlink(missing_ok=True)
                    except Exception:
                        pass
            for _ in range(3):
                shutil.rmtree(target, ignore_errors=True)
                if not target.exists():
                    break
                time.sleep(0.1)

    def _materialize_output_workspace(self, state: SandboxState):
        if state.output_workspace.exists():
            shutil.rmtree(state.output_workspace)
        state.output_workspace.mkdir(parents=True, exist_ok=True)
        self._copy_workspace(state.session_root, state.output_workspace)
        state.last_synced_hashes = self._snapshot_hashes(self._scan_workspace(state.session_root))
        state.initialized = True

    def _copy_workspace(self, source_root: Path, output_root: Path):
        for src in source_root.rglob("*"):
            if SANDBOX_DIRNAME in src.parts:
                continue
            if src == output_root or output_root in src.parents:
                continue
            rel = src.relative_to(source_root)
            dst = output_root / rel
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    def _scan_workspace(self, root: Path, *, exclude_sandbox: bool = True) -> dict[str, dict]:
        snapshot: dict[str, dict] = {}
        if not root.exists():
            return snapshot
        for path in root.rglob("*"):
            if exclude_sandbox and SANDBOX_DIRNAME in path.parts:
                continue
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            snapshot[rel] = {
                "path": path,
                "size": path.stat().st_size,
                "sha256": self._file_sha256(path),
            }
        return snapshot

    def _snapshot_hashes(self, snapshot: dict[str, dict]) -> dict[str, str | None]:
        return {rel: meta["sha256"] for rel, meta in snapshot.items()}

    def _apply_single_change(self, host_root: Path, output_root: Path, rel_path: str):
        host_path = host_root / rel_path
        out_path = output_root / rel_path
        if out_path.exists():
            host_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_path, host_path)
        elif host_path.exists():
            host_path.unlink()
            self._prune_empty_parents(host_path.parent, host_root)

    def _prune_empty_parents(self, path: Path, stop_root: Path):
        current = path
        while current != stop_root and current.exists():
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _resolve_output_working_dir(self, state: SandboxState, working_dir: str) -> Path:
        host_wd = ensure_within_workspace(working_dir)
        rel = rel_to_workspace(host_wd)
        return state.output_workspace / rel

    def _run_command_sync(self, command: str, cwd: Path, env: dict, timeout: int) -> tuple[str, str, int]:
        if IS_WINDOWS:
            return self._run_windows_command(command, cwd, env, timeout)
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            stdout_b, stderr_b = proc.communicate()
            raise TimeoutError(f"Command timed out after {timeout} seconds") from exc
        return (
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
            proc.returncode,
        )

    def _run_windows_command(self, command: str, cwd: Path, env: dict, timeout: int) -> tuple[str, str, int]:
        job = self._create_job_object()
        stdout_path = Path(tempfile.mkstemp(prefix="sandbox-stdout-", suffix=".log")[1])
        stderr_path = Path(tempfile.mkstemp(prefix="sandbox-stderr-", suffix=".log")[1])
        try:
            try:
                process_info = self._create_restricted_process(
                    command=command,
                    cwd=cwd,
                    env=env,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                )
                try:
                    self._assign_handle_to_job(job, process_info.hProcess)
                    self._resume_thread(process_info.hThread)
                    wait_result = ctypes.windll.kernel32.WaitForSingleObject(process_info.hProcess, int(timeout * 1000))
                    if wait_result == 0x00000102:
                        self._terminate_job(job)
                        raise TimeoutError(f"Command timed out after {timeout} seconds")
                    if wait_result != 0x00000000:
                        raise OSError(f"WaitForSingleObject failed: {wait_result}")

                    exit_code = wintypes.DWORD()
                    if not ctypes.windll.kernel32.GetExitCodeProcess(process_info.hProcess, ctypes.byref(exit_code)):
                        raise OSError("GetExitCodeProcess failed")
                    return (
                        stdout_path.read_text(encoding="utf-8", errors="replace"),
                        stderr_path.read_text(encoding="utf-8", errors="replace"),
                        int(exit_code.value),
                    )
                finally:
                    self._close_handle(process_info.hThread)
                    self._close_handle(process_info.hProcess)
            except Exception:
                return self._run_windows_command_fallback(command, cwd, env, timeout)
        finally:
            for path in (stdout_path, stderr_path):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
            self._close_handle(job)

    def _run_windows_command_fallback(self, command: str, cwd: Path, env: dict, timeout: int) -> tuple[str, str, int]:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NEW_PROCESS_GROUP_FLAG | CREATE_NO_WINDOW_FLAG,
        )
        try:
            stdout_b, stderr_b = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            stdout_b, stderr_b = proc.communicate()
            raise TimeoutError(f"Command timed out after {timeout} seconds") from exc
        return (
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
            proc.returncode,
        )

    def _validate_command(self, command: str) -> str | None:
        if len(command) > settings.shell_max_command_length:
            return f"Command blocked by sandbox policy: command is too long ({len(command)} chars)"

        if not settings.shell_allow_nested_shells:
            nested_shell_tokens = [
                "cmd /c",
                "cmd /k",
                "powershell",
                "pwsh",
                "bash",
                "sh ",
                "start-process",
            ]
            command_lower = command.lower()
            for token in nested_shell_tokens:
                if token in command_lower:
                    return f"Command blocked by sandbox policy: nested shell launch '{token}' is not allowed"

        if settings.shell_block_dangerous_commands:
            blocked_tokens = [token.strip().lower() for token in settings.shell_blocked_command_tokens.split(",") if token.strip()]
            command_lower = command.lower()
            for token in blocked_tokens:
                if token and token in command_lower:
                    return f"Command blocked by sandbox policy: contains disallowed token '{token}'"

        if settings.shell_reject_absolute_paths:
            absolute_path_patterns = [
                r"[a-zA-Z]:\\",
                r"\.\.[/\\]",
                r"(^|[\s'\"(])/",
            ]
            for pattern in absolute_path_patterns:
                if re.search(pattern, command):
                    return "Command blocked by sandbox policy: references absolute or parent-escaping paths"

        return None

    def _read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")

    def _build_unified_diff(self, rel_path: str, old_text: str, new_text: str) -> str:
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
        )

    def _artifact_for(self, rel_path: str, path: Path) -> ArtifactItem:
        return ArtifactItem(
            path=rel_path,
            kind=path.suffix.lower().lstrip(".") or "binary",
            size=path.stat().st_size,
            sha256=self._file_sha256(path),
            target_hint=rel_path,
        )

    def _is_text_path(self, rel_path: str) -> bool:
        path = Path(rel_path)
        if path.suffix.lower() in TEXT_EXTENSIONS:
            return True
        try:
            sample_path = self._require_session_root() / rel_path
            if sample_path.exists():
                sample_path.read_text(encoding="utf-8")
                return True
        except Exception:
            return False
        return False

    def _file_sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _profile_rank(self, profile: str) -> int:
        return 2 if profile == "exec" else 1

    def _require_session_root(self) -> Path:
        root = get_session_workspace()
        if root is None:
            raise RuntimeError("Session workspace is not set")
        return root

    def _create_job_object(self):
        if not IS_WINDOWS:
            return None
        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError("Failed to create Windows Job Object")
        info = self._job_object_limits()
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self._close_handle(job)
            raise OSError("Failed to configure Windows Job Object")
        return job

    def _assign_handle_to_job(self, job, process_handle):
        if not IS_WINDOWS or not job:
            return
        if not ctypes.windll.kernel32.AssignProcessToJobObject(job, process_handle):
            raise OSError("Failed to assign process to Windows Job Object")

    def _terminate_job(self, job):
        if not IS_WINDOWS or not job:
            return
        ctypes.windll.kernel32.TerminateJobObject(job, 1)

    def _close_handle(self, handle):
        if not IS_WINDOWS or not handle:
            return
        ctypes.windll.kernel32.CloseHandle(handle)

    def _job_object_limits(self):
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_void_p),
                ("MaximumWorkingSetSize", ctypes.c_void_p),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_void_p),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_void_p),
                ("JobMemoryLimit", ctypes.c_void_p),
                ("PeakProcessMemoryUsed", ctypes.c_void_p),
                ("PeakJobMemoryUsed", ctypes.c_void_p),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
        JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        )
        info.BasicLimitInformation.ActiveProcessLimit = 32
        return info

    def _create_restricted_process(self, command: str, cwd: Path, env: dict, stdout_path: Path, stderr_path: Path):
        if win32security is None:
            return self._create_standard_windows_process(command, cwd, env, stdout_path, stderr_path)

        try:
            token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(),
                win32con.TOKEN_DUPLICATE | win32con.TOKEN_QUERY | win32con.TOKEN_ASSIGN_PRIMARY | win32con.TOKEN_ADJUST_DEFAULT,
            )
            restricted = win32security.CreateRestrictedToken(token, win32security.DISABLE_MAX_PRIVILEGE, [], [], [])
            self._set_low_integrity_pywin32(restricted)
            return self._create_process_with_token(restricted, command, cwd, env, stdout_path, stderr_path)
        except Exception:
            return self._create_standard_windows_process(command, cwd, env, stdout_path, stderr_path)

    def _set_low_integrity_pywin32(self, token):
        if win32security is None:
            return
        integrity_sid = win32security.ConvertStringSidToSid("S-1-16-4096")
        token_info = [(integrity_sid, win32con.SE_GROUP_INTEGRITY)]
        win32security.SetTokenInformation(token, win32security.TokenIntegrityLevel, token_info)

    def _create_process_with_token(self, token, command: str, cwd: Path, env: dict, stdout_path: Path, stderr_path: Path):
        startup = win32process.STARTUPINFO()
        startup.dwFlags |= win32con.STARTF_USESTDHANDLES
        startup.hStdInput = win32api.GetStdHandle(win32api.STD_INPUT_HANDLE)
        startup.hStdOutput = self._open_inheritable_file_handle_pywin32(stdout_path)
        startup.hStdError = self._open_inheritable_file_handle_pywin32(stderr_path)
        creation_flags = (
            win32con.CREATE_SUSPENDED
            | win32con.CREATE_NEW_PROCESS_GROUP
            | win32con.CREATE_NO_WINDOW
            | win32con.CREATE_UNICODE_ENVIRONMENT
        )
        command_line = f'"{env.get("COMSPEC") or os.environ.get("COMSPEC") or "C:\\Windows\\System32\\cmd.exe"}" /d /s /c "{command}"'
        try:
            process_info = win32process.CreateProcessAsUser(
                token,
                None,
                command_line,
                None,
                None,
                True,
                creation_flags,
                env,
                str(cwd),
                startup,
            )
            return self._pywin32_process_info(process_info)
        finally:
            self._close_handle(startup.hStdOutput)
            self._close_handle(startup.hStdError)

    def _create_standard_windows_process(self, command: str, cwd: Path, env: dict, stdout_path: Path, stderr_path: Path):
        startup = win32process.STARTUPINFO() if win32process else subprocess.STARTUPINFO()
        startup.dwFlags |= win32con.STARTF_USESTDHANDLES if win32con else subprocess.STARTF_USESTDHANDLES
        stdout_handle = self._open_inheritable_file_handle_pywin32(stdout_path)
        stderr_handle = self._open_inheritable_file_handle_pywin32(stderr_path)
        startup.hStdInput = win32api.GetStdHandle(win32api.STD_INPUT_HANDLE) if win32api else None
        startup.hStdOutput = stdout_handle
        startup.hStdError = stderr_handle
        command_line = f'"{env.get("COMSPEC") or os.environ.get("COMSPEC") or "C:\\Windows\\System32\\cmd.exe"}" /d /s /c "{command}"'
        try:
            if win32process is not None:
                process_info = win32process.CreateProcess(
                    None,
                    command_line,
                    None,
                    None,
                    True,
                    CREATE_SUSPENDED_FLAG | CREATE_NEW_PROCESS_GROUP_FLAG | CREATE_NO_WINDOW_FLAG | CREATE_UNICODE_ENVIRONMENT_FLAG,
                    env,
                    str(cwd),
                    startup,
                )
                return self._pywin32_process_info(process_info)
            raise OSError("win32process is not available")
        finally:
            if win32api is not None:
                try:
                    win32api.CloseHandle(stdout_handle)
                except Exception:
                    pass
                try:
                    win32api.CloseHandle(stderr_handle)
                except Exception:
                    pass
            else:
                self._close_handle(stdout_handle)
                self._close_handle(stderr_handle)

    def _open_inheritable_file_handle_pywin32(self, path: Path):
        handle = win32file.CreateFile(
            str(path),
            win32con.GENERIC_WRITE,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            None,
            win32con.CREATE_ALWAYS,
            win32con.FILE_ATTRIBUTE_NORMAL,
            None,
        )
        win32api.SetHandleInformation(int(handle), win32con.HANDLE_FLAG_INHERIT, win32con.HANDLE_FLAG_INHERIT)
        return int(handle)

    def _resume_thread(self, thread_handle):
        if win32process is not None:
            win32process.ResumeThread(thread_handle)
            return
        if ctypes.windll.kernel32.ResumeThread(thread_handle) == 0xFFFFFFFF:
            raise OSError("ResumeThread failed")

    def _pywin32_process_info(self, process_info):
        class ProcessInfo:
            def __init__(self, h_process, h_thread):
                self.hProcess = int(h_process)
                self.hThread = int(h_thread)

        return ProcessInfo(process_info[0], process_info[1])


_sandbox_manager = SessionSandboxManager()


def get_sandbox_manager() -> SessionSandboxManager:
    return _sandbox_manager


def sync_result_to_json(result: SyncResult) -> str:
    payload = {
        "profile": result.profile,
        "change_set": result.change_set,
        "diff_text": result.diff_text,
        "artifacts": [item.__dict__ for item in result.artifacts],
        "conflicts": result.conflicts,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
