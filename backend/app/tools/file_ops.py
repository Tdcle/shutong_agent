"""File operation tools with sandbox-backed writes.

Reads operate on the host session workspace.
Writes, edits, moves, and deletes are applied inside the session sandbox output
workspace first, then synced back to the host workspace as audited changes.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
from pathlib import Path

from app.tools.base import tool
from app.tools.sandbox import get_sandbox_manager
from app.tools.workspace import ensure_within_workspace, resolve as _resolve

MAX_READ_SIZE = 200 * 1024


@tool(
    name="read_file",
    description="Read a file from the current session workspace.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
            "encoding": {"type": "string", "description": "Text encoding, defaults to utf-8"},
        },
        "required": ["path"],
    },
    permission_level="read",
)
def read_file(path: str, encoding: str = "utf-8") -> str:
    p = _resolve(path)
    if not p.exists():
        return f"Error: file does not exist: {path}"
    if not p.is_file():
        return f"Error: path is not a file: {path}"
    try:
        content = p.read_text(encoding=encoding)
        if len(content) > MAX_READ_SIZE:
            content = content[:MAX_READ_SIZE] + f"\n\n... (truncated, total {len(content)} characters)"
        return content
    except Exception as exc:
        return f"Read failed: {exc}"


@tool(
    name="write_file",
    description="Create or overwrite a file inside the sandbox-backed session workspace.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
            "content": {"type": "string", "description": "File content"},
        },
        "required": ["path", "content"],
    },
    permission_level="write",
)
def write_file(path: str, content: str) -> str:
    sandbox = get_sandbox_manager()
    try:
        state, host_path, sandbox_path, _ = sandbox.writable_path(path, profile="edit")
        existed = host_path.exists()
        sandbox_path.parent.mkdir(parents=True, exist_ok=True)
        sandbox_path.write_text(content, encoding="utf-8")
        sync_result = sandbox.sync_back(state)
        action = "updated" if existed else "created"
        return f"File {action}: {path} ({len(content)} chars)\n{sync_result.to_summary()}"
    except ValueError as exc:
        return f"Path rejected: {exc}"
    except Exception as exc:
        return f"Write failed: {exc}"


@tool(
    name="edit_file",
    description="Replace a unique substring in a file inside the sandbox-backed workspace.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
            "old_string": {"type": "string", "description": "Unique source text to replace"},
            "new_string": {"type": "string", "description": "Replacement text"},
        },
        "required": ["path", "old_string", "new_string"],
    },
    permission_level="write",
)
def edit_file(path: str, old_string: str, new_string: str) -> str:
    sandbox = get_sandbox_manager()
    try:
        state, host_path, sandbox_path, _ = sandbox.writable_path(path, profile="edit")
    except ValueError as exc:
        return f"Path rejected: {exc}"

    if not host_path.exists():
        return f"Error: file does not exist: {path}"
    if not host_path.is_file():
        return f"Error: path is not a file: {path}"

    try:
        content = sandbox_path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Read failed: {exc}"

    count = content.count(old_string)
    if count == 0:
        return "Edit failed: old_string was not found in the file."
    if count > 1:
        lines = content.splitlines()
        locations = []
        for idx, line in enumerate(lines, 1):
            if old_string in line:
                locations.append(f"  line {idx}: {line.strip()[:100]}")
        preview = "\n".join(locations[:10])
        return f"Edit failed: old_string matched {count} times and must be unique.\nMatches:\n{preview}"

    new_content = content.replace(old_string, new_string, 1)
    try:
        sandbox_path.write_text(new_content, encoding="utf-8")
        sync_result = sandbox.sync_back(state)
        return f"File edited: {path}\n{sync_result.to_summary()}"
    except Exception as exc:
        return f"Write failed: {exc}"


@tool(
    name="grep",
    description="Search file contents with a regex pattern.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern"},
            "path": {"type": "string", "description": "Search root, defaults to current workspace"},
            "glob": {"type": "string", "description": "Optional file glob filter, for example *.py"},
            "context_lines": {"type": "integer", "description": "Context lines before and after each match"},
            "case_sensitive": {"type": "boolean", "description": "Whether the regex should be case sensitive"},
        },
        "required": ["pattern"],
    },
    permission_level="read",
)
def grep(
    pattern: str,
    path: str = ".",
    glob: str = "",
    context_lines: int = 0,
    case_sensitive: bool = True,
) -> str:
    search_dir = _resolve(path)
    if not search_dir.exists():
        return f"Error: directory does not exist: {path}"

    try:
        return _grep_rg(pattern, search_dir, glob, context_lines, case_sensitive)
    except Exception:
        return _grep_python(pattern, search_dir, glob, context_lines, case_sensitive)


def _grep_rg(pattern: str, search_dir: Path, glob: str, context_lines: int, case_sensitive: bool) -> str:
    args = ["rg", "--line-number", "--no-heading", "--color=never"]
    if not case_sensitive:
        args.append("--ignore-case")
    if glob:
        args.extend(["--glob", glob])
    if context_lines > 0:
        args.extend(["-C", str(context_lines)])
    args.extend([pattern, str(search_dir)])

    result = subprocess.run(args, capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    output = result.stdout.strip()
    if not output:
        if result.returncode == 1:
            return f"No matches found for '{pattern}'."
        return f"grep failed: {result.stderr.strip()}"
    return _format_grep_output(output)


def _grep_python(pattern: str, search_dir: Path, glob: str, context_lines: int, case_sensitive: bool) -> str:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return f"Invalid regex: {exc}"

    if glob:
        files = []
        for candidate in search_dir.rglob("*"):
            if candidate.is_file():
                rel = str(candidate.relative_to(search_dir))
                if fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(candidate.name, glob):
                    files.append(candidate)
    else:
        files = [candidate for candidate in search_dir.rglob("*") if candidate.is_file()]

    matches: list[str] = []
    for file_path in files[:200]:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lines = content.splitlines()
        for line_num, line in enumerate(lines, 1):
            if not regex.search(line):
                continue
            rel_path = file_path.relative_to(search_dir)
            if context_lines > 0:
                start = max(0, line_num - context_lines - 1)
                end = min(len(lines), line_num + context_lines)
                ctx = []
                for ctx_line_num in range(start + 1, end + 1):
                    marker = ">" if ctx_line_num == line_num else " "
                    ctx.append(f"  {marker}{ctx_line_num}: {lines[ctx_line_num - 1][:200]}")
                matches.append(f"{rel_path}:\n" + "\n".join(ctx))
            else:
                matches.append(f"{rel_path}:{line_num}: {line.strip()[:200]}")
            if len(matches) >= 50:
                break
        if len(matches) >= 50:
            break

    if not matches:
        return f"No matches found for '{pattern}'."
    result = "\n".join(matches)
    if len(matches) >= 50:
        result += "\n\n... results truncated (first 50 matches shown) ..."
    return result


def _format_grep_output(output: str) -> str:
    lines = output.splitlines()
    if len(lines) <= 80:
        return output
    return "\n".join(lines[:80]) + f"\n\n... results truncated (showing first 80 of {len(lines)} lines) ..."


@tool(
    name="glob",
    description="Find files recursively by glob pattern.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, for example **/*.py"},
            "path": {"type": "string", "description": "Search root, defaults to current workspace"},
        },
        "required": ["pattern"],
    },
    permission_level="read",
)
def glob(pattern: str, path: str = ".") -> str:
    search_dir = _resolve(path)
    if not search_dir.exists():
        return f"Error: directory does not exist: {path}"

    try:
        files = sorted(search_dir.glob(pattern))
    except Exception as exc:
        return f"Invalid glob pattern: {exc}"

    excluded_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", "dist", "build", ".next"}
    filtered = []
    for file_path in files:
        if set(file_path.parts) & excluded_dirs:
            continue
        rel = file_path.relative_to(search_dir)
        suffix = "/" if file_path.is_dir() else ""
        filtered.append(f"  {rel}{suffix}")

    if not filtered:
        return f"No files matched '{pattern}'."
    result = "\n".join(filtered[:100])
    if len(filtered) > 100:
        result += f"\n  ... total {len(filtered)} matches, showing first 100 ..."
    return result


@tool(
    name="move_file",
    description="Move or rename a file inside the sandbox-backed workspace.",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Source path"},
            "destination": {"type": "string", "description": "Destination path"},
        },
        "required": ["source", "destination"],
    },
    permission_level="write",
)
def move_file(source: str, destination: str) -> str:
    sandbox = get_sandbox_manager()
    try:
        state, source_host, source_out, _ = sandbox.writable_path(source, profile="edit")
        _, _, dest_out, _ = sandbox.writable_path(destination, profile="edit")
    except ValueError as exc:
        return f"Path rejected: {exc}"

    if not source_host.exists():
        return f"Error: source path does not exist: {source}"

    try:
        source_out.parent.mkdir(parents=True, exist_ok=True)
        dest_out.parent.mkdir(parents=True, exist_ok=True)
        if not source_out.exists() and source_host.is_file():
            shutil.copy2(source_host, source_out)
        elif not source_out.exists() and source_host.is_dir():
            shutil.copytree(source_host, source_out)
        shutil.move(str(source_out), str(dest_out))
        sync_result = sandbox.sync_back(state)
        return f"Moved: {source} -> {destination}\n{sync_result.to_summary()}"
    except Exception as exc:
        return f"Move failed: {exc}"


@tool(
    name="delete_file",
    description="Delete a file or an empty directory inside the sandbox-backed workspace.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to delete"},
        },
        "required": ["path"],
    },
    permission_level="destroy",
)
def delete_file(path: str) -> str:
    sandbox = get_sandbox_manager()
    try:
        state, host_path, sandbox_path, _ = sandbox.writable_path(path, profile="edit")
    except ValueError as exc:
        return f"Path rejected: {exc}"

    if not host_path.exists():
        return f"Error: path does not exist: {path}"

    try:
        if host_path.is_dir():
            if not sandbox_path.exists():
                sandbox_path.mkdir(parents=True, exist_ok=True)
            sandbox_path.rmdir()
            sync_result = sandbox.sync_back(state)
            return f"Deleted empty directory: {path}\n{sync_result.to_summary()}"
        if sandbox_path.exists():
            sandbox_path.unlink()
        else:
            sandbox_path.parent.mkdir(parents=True, exist_ok=True)
        sync_result = sandbox.sync_back(state)
        return f"Deleted file: {path}\n{sync_result.to_summary()}"
    except OSError as exc:
        return f"Delete failed (directory may be non-empty or permission denied): {exc}"
    except Exception as exc:
        return f"Delete failed: {exc}"


@tool(
    name="list_files",
    description="List files or directories from the current session workspace.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path"},
            "pattern": {"type": "string", "description": "Optional glob pattern"},
        },
        "required": ["path"],
    },
    permission_level="read",
)
def list_files(path: str, pattern: str = "*") -> str:
    p = _resolve(path)
    if not p.exists():
        return f"Error: directory does not exist: {path}"
    if not p.is_dir():
        return f"Error: path is not a directory: {path}"
    files = sorted(p.glob(pattern))
    if not files:
        return f"No files matched '{pattern}' in {path}."
    lines = []
    for item in files[:50]:
        suffix = "/" if item.is_dir() else ""
        lines.append(f"  {item.name}{suffix}")
    result = "\n".join(lines)
    if len(files) > 50:
        result += f"\n  ... total {len(files)} entries, showing first 50 ..."
    return result
