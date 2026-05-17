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
import shlex
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

# Security preamble prepended to execute_python code
PYTHON_SECURITY_PREAMBLE = '''\
import sys as __agent_sys
import importlib.abc as __agent_abc
import builtins as __agent_b

# ---- Block dangerous module imports (via sys.meta_path) ----
_AGENT_BLOCKED_MODULES = frozenset({
    # Network — prevent data exfiltration
    "socket", "requests", "urllib.request", "urllib3", "httpx",
    "aiohttp", "http.client", "http.server", "ftplib", "smtplib",
    "telnetlib", "poplib",
    # Dynamic code — prevent code injection
    "pdb", "code",
})

class __agent_ImportBlocker(__agent_abc.MetaPathFinder):
    _blocked = _AGENT_BLOCKED_MODULES
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] in self._blocked:
            raise ImportError(f"Module '{fullname}' is blocked for security reasons")
        return None

__agent_sys.meta_path.insert(0, __agent_ImportBlocker())

# ---- Replace builtins.__import__ with safe wrapper ----
def __agent_make_safe_import(blocked, original):
    def _safe_import(name, *args, **kwargs):
        if name.split(".")[0] in blocked:
            raise ImportError(f"Module '{name}' is blocked for security reasons")
        return original(name, *args, **kwargs)
    return _safe_import

__agent_b.__import__ = __agent_make_safe_import(_AGENT_BLOCKED_MODULES, __agent_b.__import__)

del __agent_sys, __agent_abc, __agent_b, __agent_ImportBlocker
del __agent_make_safe_import, _AGENT_BLOCKED_MODULES

# ---- Neuter dangerous functions in otherwise-importable modules ----
def __agent_blocked_call(*_a, **_kw):
    raise RuntimeError("This function is blocked for security reasons")

try:
    import subprocess as __agent_sp
    for __agent_fn in ("run", "call", "check_call", "check_output", "Popen"):
        setattr(__agent_sp, __agent_fn, __agent_blocked_call)
    del __agent_sp
except ImportError:
    pass

try:
    import shlex as __agent_sl
    for __agent_fn in ("split",):
        setattr(__agent_sl, __agent_fn, __agent_blocked_call)
    del __agent_sl
except ImportError:
    pass

del __agent_blocked_call
# === END SECURITY PREAMBLE ===
'''

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
    external_bindings: dict[str, str] = field(default_factory=dict)
    external_source_hashes: dict[str, dict[str, str | None]] = field(default_factory=dict)
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
        if host_path.exists() and not output_path.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if host_path.is_dir():
                shutil.copytree(host_path, output_path)
            else:
                shutil.copy2(host_path, output_path)
                state.last_synced_hashes[rel_path] = self._file_sha256(host_path)
        return state, host_path, output_path, rel_path

    def external_writable_path(self, path: str, profile: str = "edit") -> tuple[SandboxState, Path, Path, str]:
        self.close_inactive()
        state = self.ensure(profile)
        host_path = Path(path).expanduser().resolve()
        if self._is_within_session_workspace(host_path):
            rel_path = rel_to_workspace(host_path)
            return state, host_path, state.output_workspace / rel_path, rel_path

        rel_root = Path(".external") / self._external_overlay_rel(host_path)
        output_path = state.output_workspace / rel_root
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if host_path.exists():
            if host_path.is_dir():
                if output_path.exists():
                    shutil.rmtree(output_path, ignore_errors=True)
                shutil.copytree(host_path, output_path)
            else:
                shutil.copy2(host_path, output_path)
        rel_key = rel_root.as_posix()
        state.external_bindings[rel_key] = str(host_path)
        state.external_source_hashes[rel_key] = self._snapshot_external_source(host_path)
        return state, host_path, output_path, rel_root.as_posix()

    async def run_command(self, command: str, working_dir: str = ".", timeout_seconds: int | None = None) -> CommandResult:
        state = self.ensure("exec")
        blocked_reason = self._validate_command(command)
        if blocked_reason:
            return self._blocked_result(state, blocked_reason)
        try:
            output_cwd = self._resolve_output_working_dir(state, working_dir)
        except ValueError as exc:
            return self._path_error_result(state, exc)

        output_cwd, restricted_env, _ = self._prepare_execution_context(state, output_cwd)

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

    async def run_python(self, code: str, working_dir: str = ".", timeout_seconds: int | None = None) -> CommandResult:
        state = self.ensure("exec")
        python_executable = Path(settings.agent_python_executable)
        if not python_executable.exists():
            return self._blocked_result(
                state,
                f"Agent Python runtime not found: {python_executable}",
            )

        try:
            output_cwd = self._resolve_output_working_dir(state, working_dir)
        except ValueError as exc:
            return self._path_error_result(state, exc)

        output_cwd, restricted_env, sandbox_tmp = self._prepare_execution_context(state, output_cwd)
        script_path = sandbox_tmp / f"agent_exec_{int(time.time() * 1000)}.py"

        # Prepend security preamble to restrict dangerous builtins and imports
        full_code = PYTHON_SECURITY_PREAMBLE + "\n" + code
        script_path.write_text(full_code, encoding="utf-8")

        timeout = timeout_seconds or settings.python_timeout_seconds
        try:
            stdout, stderr, returncode = await asyncio.to_thread(
                self._run_program_sync,
                python_executable,
                [str(script_path)],
                output_cwd,
                restricted_env,
                timeout,
            )
        finally:
            try:
                script_path.unlink(missing_ok=True)
            except Exception:
                pass

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
        external_file_bindings = {
            rel_path
            for rel_path, source_hashes in state.external_source_hashes.items()
            if "" in source_hashes
        }
        all_paths = sorted(host_paths | output_paths | external_file_bindings)
        changed_paths: list[str] = []
        touched_external_roots: set[str] = set()

        for rel_path in all_paths:
            external_binding = self._external_binding_for_rel(state, rel_path)
            if external_binding is not None:
                overlay_root_rel, host_root, host_path, host_rel = external_binding
                expected_hash = state.external_source_hashes.get(overlay_root_rel, {}).get(host_rel)
                out_meta = output_snapshot.get(rel_path)
                out_hash = out_meta["sha256"] if out_meta else None
                current_hash = self._current_hash(host_path)
                if out_meta is None or current_hash != expected_hash or out_hash != expected_hash:
                    changed_paths.append(rel_path)
                continue

            host_meta = host_snapshot.get(rel_path)
            out_meta = output_snapshot.get(rel_path)
            host_hash = host_meta["sha256"] if host_meta else None
            out_hash = out_meta["sha256"] if out_meta else None
            if host_hash == out_hash:
                continue
            changed_paths.append(rel_path)

        conflicts: list[str] = []
        for rel_path in changed_paths:
            external_binding = self._external_binding_for_rel(state, rel_path)
            if external_binding is not None:
                overlay_root_rel, host_root, host_path, host_rel = external_binding
                expected_hash = state.external_source_hashes.get(overlay_root_rel, {}).get(host_rel)
                current_hash = self._current_hash(host_path)
                if expected_hash != current_hash:
                    conflicts.append(f"{rel_path} -> {host_path}")
                continue

            expected_hash = state.last_synced_hashes.get(rel_path)
            current_hash = host_snapshot.get(rel_path, {}).get("sha256")
            if expected_hash != current_hash:
                conflicts.append(rel_path)

        if conflicts:
            return SyncResult(profile=state.profile, change_set=change_set, conflicts=conflicts)

        for rel_path in changed_paths:
            external_binding = self._external_binding_for_rel(state, rel_path)
            if external_binding is not None:
                overlay_root_rel, host_root, host_path, host_rel = external_binding
                out_meta = output_snapshot.get(rel_path)
                out_exists = out_meta is not None
                host_exists = host_path.exists()
                is_text = self._is_text_path(rel_path) or self._is_text_path(str(host_path))
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
                        diff_chunks.append(self._build_unified_diff(rel_path, self._read_text(host_path), ""))
                    else:
                        change_set["deleted_binary_files"].append(rel_path)
                else:
                    if is_text:
                        change_set["modified_text_files"].append(rel_path)
                        diff_chunks.append(
                            self._build_unified_diff(
                                rel_path,
                                self._read_text(host_path),
                                self._read_text(out_meta["path"]),
                            )
                        )
                    else:
                        change_set["modified_binary_files"].append(rel_path)
                        artifacts.append(self._artifact_for(rel_path, out_meta["path"]))

                self._apply_external_change(host_root, host_path, state.output_workspace / rel_path)
                touched_external_roots.add(overlay_root_rel)
                continue

            out_meta = output_snapshot.get(rel_path)
            out_exists = out_meta is not None
            host_meta = host_snapshot.get(rel_path)
            host_exists = host_meta is not None
            host_path = state.session_root / rel_path
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
                    diff_chunks.append(self._build_unified_diff(rel_path, self._read_text(host_path), ""))
                else:
                    change_set["deleted_binary_files"].append(rel_path)
            else:
                if is_text:
                    change_set["modified_text_files"].append(rel_path)
                    diff_chunks.append(
                        self._build_unified_diff(
                            rel_path,
                            self._read_text(host_path),
                            self._read_text(out_meta["path"]),
                        )
                    )
                else:
                    change_set["modified_binary_files"].append(rel_path)
                    artifacts.append(self._artifact_for(rel_path, out_meta["path"]))

            self._apply_single_change(state.session_root, state.output_workspace, rel_path)

        state.last_synced_hashes = self._snapshot_hashes(self._scan_workspace(state.session_root))
        for overlay_root_rel in touched_external_roots:
            host_root = Path(state.external_bindings[overlay_root_rel])
            state.external_source_hashes[overlay_root_rel] = self._snapshot_external_source(host_root)
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

    def _apply_external_change(self, host_root: Path, host_path: Path, out_path: Path):
        if out_path.exists():
            if out_path.is_dir():
                if host_path.exists():
                    if host_path.is_file():
                        host_path.unlink()
                    else:
                        shutil.rmtree(host_path, ignore_errors=True)
                host_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(out_path, host_path, dirs_exist_ok=True)
            else:
                host_path.parent.mkdir(parents=True, exist_ok=True)
                if host_path.exists() and host_path.is_dir():
                    shutil.rmtree(host_path, ignore_errors=True)
                shutil.copy2(out_path, host_path)
        elif host_path.exists():
            if host_path.is_dir():
                shutil.rmtree(host_path, ignore_errors=False)
            else:
                host_path.unlink()
                self._prune_empty_parents(host_path.parent, host_root.parent)

    def _prune_empty_parents(self, path: Path, stop_root: Path):
        current = path
        while current != stop_root and current.exists():
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _resolve_output_working_dir(self, state: SandboxState, working_dir: str) -> Path:
        try:
            host_wd = ensure_within_workspace(working_dir)
        except ValueError as e:
            raise ValueError(
                f"{e}\n"
                "Tip: To process files outside the workspace, first use copy_file to stage "
                "them into the workspace, then use execute_python/execute_bash on the copy."
            ) from e
        rel = rel_to_workspace(host_wd)
        return state.output_workspace / rel

    def _empty_change_set(self) -> dict[str, list[str]]:
        return {
            "added_text_files": [],
            "added_binary_files": [],
            "modified_text_files": [],
            "modified_binary_files": [],
            "deleted_text_files": [],
            "deleted_binary_files": [],
        }

    def _blocked_result(self, state: SandboxState, message: str) -> CommandResult:
        return CommandResult(
            stdout="",
            stderr=message,
            exit_code=-1,
            success=False,
            sync_result=SyncResult(profile=state.profile, change_set=self._empty_change_set()),
        )

    def _path_error_result(self, state: SandboxState, exc: Exception) -> CommandResult:
        return CommandResult(
            stdout="",
            stderr=str(exc),
            exit_code=-1,
            success=False,
            sync_result=SyncResult(profile=state.profile, change_set=self._empty_change_set()),
        )

    def _prepare_execution_context(self, state: SandboxState, output_cwd: Path) -> tuple[Path, dict, Path]:
        output_cwd.mkdir(parents=True, exist_ok=True)
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
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
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
        return output_cwd, restricted_env, sandbox_tmp

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

    def _run_program_sync(
        self,
        executable: Path,
        args: list[str],
        cwd: Path,
        env: dict,
        timeout: int,
    ) -> tuple[str, str, int]:
        if IS_WINDOWS:
            return self._run_windows_program(executable, args, cwd, env, timeout)
        proc = subprocess.Popen(
            [str(executable), *args],
            shell=False,
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
        # Use Git Bash on Windows — bash syntax is what LLMs are trained on
        bash_candidates = [
            "C:\\Program Files\\Git\\bin\\bash.exe",
            "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
        ]
        bash_exe = None
        for candidate in bash_candidates:
            if Path(candidate).exists():
                bash_exe = candidate
                break
        if bash_exe is None:
            # Fallback: try PATH
            bash_exe = "bash.exe"
        # Escape double quotes and backslashes for bash -c
        escaped = command.replace("\\", "\\\\").replace('"', '\\"')
        command_line = f'"{bash_exe}" -c "{escaped}"'
        return self._run_windows_process(command_line, cwd, env, timeout)

    def _run_windows_program(
        self,
        executable: Path,
        args: list[str],
        cwd: Path,
        env: dict,
        timeout: int,
    ) -> tuple[str, str, int]:
        argv = [str(executable), *args]
        command_line = subprocess.list2cmdline(argv)
        return self._run_windows_process(command_line, cwd, env, timeout, application=str(executable), argv=argv)

    def _run_windows_process(
        self,
        command_line: str,
        cwd: Path,
        env: dict,
        timeout: int,
        *,
        application: str | None = None,
        argv: list[str] | None = None,
    ) -> tuple[str, str, int]:
        job = self._create_job_object()
        stdout_path = Path(tempfile.mkstemp(prefix="sandbox-stdout-", suffix=".log")[1])
        stderr_path = Path(tempfile.mkstemp(prefix="sandbox-stderr-", suffix=".log")[1])
        try:
            try:
                process_info = self._create_restricted_process(
                    command_line=command_line,
                    cwd=cwd,
                    env=env,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    application=application,
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
                return self._run_windows_process_fallback(
                    command_line,
                    cwd,
                    env,
                    timeout,
                    application=application,
                    argv=argv,
                )
        finally:
            for path in (stdout_path, stderr_path):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
            self._close_handle(job)

    def _run_windows_process_fallback(
        self,
        command_line: str,
        cwd: Path,
        env: dict,
        timeout: int,
        *,
        application: str | None = None,
        argv: list[str] | None = None,
    ) -> tuple[str, str, int]:
        command = argv if application else command_line
        shell = application is None
        proc = subprocess.Popen(
            command,
            shell=shell,
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
        """Validate a shell command before execution.

        Split into two tiers:

        Tier 1 — Always blocked (malware / sandbox escape):
          These patterns are dangerous regardless of user approval.
          Even if the user clicked "allow", they should never run.

        Tier 2 — Advisory (user-approved shells skip these):
          Absolute paths, parent escapes, etc. The user explicitly approved
          the shell execution, so they accept the risk.  Runtime confinement
          (cwd, env vars, sync_back diff audit) still applies.
        """
        if len(command) > settings.shell_max_command_length:
            return f"Command blocked by sandbox policy: command is too long ({len(command)} chars)"

        # ── Tier 1: always-blocked patterns ──────────────────────────

        if not settings.shell_allow_nested_shells:
            nested_shell_tokens = [
                "cmd /c",
                "cmd /k",
                "powershell",
                "pwsh",
                "sh ",
                "zsh",
                "start-process",
            ]
            command_lower = command.lower()
            for token in nested_shell_tokens:
                if token in command_lower:
                    return f"Command blocked by sandbox policy: nested shell launch '{token}' is not allowed"

        if settings.shell_block_dangerous_commands:
            blocked_tokens = [token.strip().lower() for token in settings.shell_blocked_command_tokens.split(",") if token.strip()]
            command_lower = command.lower()
            command_words = self._command_words(command)
            for token in blocked_tokens:
                if not token:
                    continue
                if " " in token:
                    if token in command_lower:
                        return f"Command blocked by sandbox policy: contains disallowed token '{token}'"
                    continue
                if token in command_words:
                    return f"Command blocked by sandbox policy: contains disallowed token '{token}'"

        external_batch_reason = self._external_batch_command_reason(command)
        if external_batch_reason:
            return external_batch_reason

        return None

    def _external_batch_command_reason(self, command: str) -> str | None:
        lowered = command.lower()
        destructive_tokens = ("del ", "erase ", "remove-item", "move ", "mv ", "ren ", "rename-item")
        if not any(token in lowered for token in destructive_tokens):
            return None

        wildcard_tokens = ("*", "?")
        has_wildcard = any(token in command for token in wildcard_tokens)
        if not has_wildcard:
            return None

        drive_path = re.search(r"[A-Za-z]:\\", command)
        parent_escape = "..\\" in command or "../" in command
        if not drive_path and not parent_escape:
            return None

        return (
            "Command blocked by sandbox policy: batch destructive operations outside the workspace "
            "must use move_paths, delete_paths, or copy_paths with exact paths."
        )

    def _command_words(self, command: str) -> set[str]:
        normalized = command.replace("\r", " ").replace("\n", " ")
        try:
            parts = shlex.split(normalized, posix=False)
        except ValueError:
            parts = re.split(r"\s+", normalized)
        words: set[str] = set()
        for part in parts:
            piece = part.strip().strip("\"'").lower()
            if not piece:
                continue
            words.add(piece)
            for fragment in re.split(r"[^a-z0-9_./:-]+", piece):
                if fragment:
                    words.add(fragment)
        return words

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

    def _snapshot_external_source(self, path: Path) -> dict[str, str | None]:
        snapshot: dict[str, str | None] = {}
        if not path.exists():
            return snapshot
        if path.is_file():
            snapshot[""] = self._file_sha256(path)
            return snapshot
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            snapshot[item.relative_to(path).as_posix()] = self._file_sha256(item)
        return snapshot

    def _external_binding_for_rel(self, state: SandboxState, rel_path: str) -> tuple[str, Path, Path, str] | None:
        rel_str = Path(rel_path).as_posix()
        best: tuple[str, Path, Path, str] | None = None
        for overlay_root_rel, host_root_str in state.external_bindings.items():
            if rel_str != overlay_root_rel and not rel_str.startswith(f"{overlay_root_rel}/"):
                continue
            host_root = Path(host_root_str)
            host_rel = rel_str[len(overlay_root_rel):].lstrip("/")
            host_path = host_root if not host_rel else host_root / Path(host_rel)
            if best is None or len(overlay_root_rel) > len(best[0]):
                best = (overlay_root_rel, host_root, host_path, host_rel)
        return best

    def _current_hash(self, path: Path) -> str | None:
        if not path.exists():
            return None
        if path.is_dir():
            return None
        return self._file_sha256(path)

    def _is_within_session_workspace(self, path: Path) -> bool:
        root = self._require_session_root().resolve()
        candidate = path.resolve()
        return candidate == root or root in candidate.parents

    def _external_overlay_rel(self, host_path: Path) -> str:
        digest = hashlib.sha1(str(host_path).encode("utf-8", errors="replace")).hexdigest()[:16]
        name = host_path.name or "item"
        return f"{digest}/{name}"

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

    def _create_restricted_process(
        self,
        command_line: str,
        cwd: Path,
        env: dict,
        stdout_path: Path,
        stderr_path: Path,
        *,
        application: str | None = None,
    ):
        if win32security is None:
            return self._create_standard_windows_process(
                command_line,
                cwd,
                env,
                stdout_path,
                stderr_path,
                application=application,
            )

        try:
            token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(),
                win32con.TOKEN_DUPLICATE | win32con.TOKEN_QUERY | win32con.TOKEN_ASSIGN_PRIMARY | win32con.TOKEN_ADJUST_DEFAULT,
            )
            restricted = win32security.CreateRestrictedToken(token, win32security.DISABLE_MAX_PRIVILEGE, [], [], [])
            self._set_low_integrity_pywin32(restricted)
            return self._create_process_with_token(
                restricted,
                command_line,
                cwd,
                env,
                stdout_path,
                stderr_path,
                application=application,
            )
        except Exception:
            return self._create_standard_windows_process(
                command_line,
                cwd,
                env,
                stdout_path,
                stderr_path,
                application=application,
            )

    def _set_low_integrity_pywin32(self, token):
        if win32security is None:
            return
        integrity_sid = win32security.ConvertStringSidToSid("S-1-16-4096")
        token_info = [(integrity_sid, win32con.SE_GROUP_INTEGRITY)]
        win32security.SetTokenInformation(token, win32security.TokenIntegrityLevel, token_info)

    def _create_process_with_token(
        self,
        token,
        command_line: str,
        cwd: Path,
        env: dict,
        stdout_path: Path,
        stderr_path: Path,
        *,
        application: str | None = None,
    ):
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
        try:
            process_info = win32process.CreateProcessAsUser(
                token,
                application,
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

    def _create_standard_windows_process(
        self,
        command_line: str,
        cwd: Path,
        env: dict,
        stdout_path: Path,
        stderr_path: Path,
        *,
        application: str | None = None,
    ):
        startup = win32process.STARTUPINFO() if win32process else subprocess.STARTUPINFO()
        startup.dwFlags |= win32con.STARTF_USESTDHANDLES if win32con else subprocess.STARTF_USESTDHANDLES
        stdout_handle = self._open_inheritable_file_handle_pywin32(stdout_path)
        stderr_handle = self._open_inheritable_file_handle_pywin32(stderr_path)
        startup.hStdInput = win32api.GetStdHandle(win32api.STD_INPUT_HANDLE) if win32api else None
        startup.hStdOutput = stdout_handle
        startup.hStdError = stderr_handle
        try:
            if win32process is not None:
                process_info = win32process.CreateProcess(
                    application,
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
