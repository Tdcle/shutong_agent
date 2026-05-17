"""文件工具：支持基于沙箱的工作区写入，以及结构化的外部文件选择。"""

from __future__ import annotations

import contextvars
import difflib
import fnmatch
import re
import shutil
import subprocess
from pathlib import Path

from app.tools.base import tool
from app.tools.permissions import PathCapability
from app.tools.sandbox import get_sandbox_manager
from app.tools.workspace import get_session_workspace, resolve_readonly, resolve_with_capability

MAX_READ_SIZE = 200 * 1024

# Per-session tracking of files that have been read (for read-before-write enforcement)
_current_read_files: contextvars.ContextVar[set[str] | None] = contextvars.ContextVar(
    "session_read_files",
    default=None,
)


def _get_read_files() -> set[str]:
    files = _current_read_files.get()
    if files is None:
        files = set()
        _current_read_files.set(files)
    return files


def _record_read(path: str):
    _get_read_files().add(Path(path).expanduser().resolve().as_posix())


def _was_read(path: str) -> bool:
    return Path(path).expanduser().resolve().as_posix() in _get_read_files()


def _build_diff(file_path: str, old_text: str, new_text: str) -> str:
    """Build a unified diff for auditing."""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
    ).strip()
    if not diff:
        return "(no changes)"
    max_len = 3000
    if len(diff) > max_len:
        diff = diff[:max_len] + "\n... diff truncated ..."
    return diff


def _fuzzy_find_in_content(content: str, old_string: str) -> tuple[str | None, int]:
    """Try whitespace-normalized matching when exact match fails.

    Returns (exact_match_from_file | None, match_count).
    """
    # First pass: exact match
    count = content.count(old_string)
    if count == 1:
        return old_string, 1
    if count > 1:
        return None, count

    # Second pass: normalize trailing whitespace per line + collapse leading whitespace
    def _normalize(text: str) -> list[str]:
        return [" ".join(line.rstrip().split()) for line in text.splitlines()]

    norm_content_lines = _normalize(content)
    norm_old_lines = _normalize(old_string)

    if not norm_old_lines or not norm_content_lines:
        return None, 0

    # Find where the normalized old_string starts in the normalized content
    # Line-by-line sliding window
    ol = len(norm_old_lines)
    cl = len(norm_content_lines)
    matches: list[int] = []
    for i in range(cl - ol + 1):
        if norm_content_lines[i : i + ol] == norm_old_lines:
            matches.append(i)

    if len(matches) != 1:
        return None, len(matches)

    # Extract the exact text from the original content at the matched line range
    start_line = matches[0]
    end_line = start_line + ol - 1
    original_lines = content.splitlines(keepends=True)
    exact_match = "".join(original_lines[start_line : end_line + 1])
    # Strip trailing newline if old_string didn't have one
    if not old_string.endswith("\n") and exact_match.endswith("\n"):
        exact_match = exact_match.rstrip("\n")
    return exact_match, 1


def _prepare_mutable_path(
    sandbox,
    raw_path: str,
    *,
    tool_name: str,
    arg_name: str,
    capability: PathCapability,
):
    resolved_path, managed = resolve_with_capability(
        raw_path,
        tool_name=tool_name,
        arg_name=arg_name,
        capability=capability,
    )
    if managed:
        return sandbox.writable_path(raw_path, profile="edit")
    return sandbox.external_writable_path(str(resolved_path), profile="edit")


@tool(
    name="read_file",
    description="读取当前会话可访问范围内的单个文件内容。支持按行号分页读取大文件。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "offset": {
                "type": "integer",
                "description": "起始行号（1-based），不传则从第 1 行开始。读取大文件时建议分页使用。",
            },
            "limit": {
                "type": "integer",
                "description": "最多读取的行数，不传则读取全部（最多 200KB）。配合 offset 分页读取大文件。",
            },
            "encoding": {"type": "string", "description": "文本编码，默认 utf-8"},
        },
        "required": ["path"],
    },
    permission_level="read",
)
def read_file(path: str, encoding: str = "utf-8", offset: int = 0, limit: int = 0) -> str:
    p = resolve_readonly(path)
    if not p.exists():
        return f"Error: file does not exist: {path}"
    if not p.is_file():
        return f"Error: path is not a file: {path}"

    _record_read(path)

    try:
        raw = p.read_text(encoding=encoding)
        total_lines = len(raw.splitlines())
        total_chars = len(raw)

        if offset > 0 or limit > 0:
            lines = raw.splitlines(keepends=True)
            start = max(0, offset - 1) if offset > 0 else 0
            if limit > 0:
                end = min(start + limit, len(lines))
            else:
                end = len(lines)
            selected = lines[start:end]
            content = "".join(selected)
            header = f"[Lines {start + 1}-{end} / {total_lines}, {len(content)}/{total_chars} chars]\n"
            return header + content

        if len(raw) > MAX_READ_SIZE:
            content = raw[:MAX_READ_SIZE] + f"\n\n... (truncated, total {total_chars} characters, {total_lines} lines) ..."
            return content
        return raw
    except UnicodeDecodeError:
        ext = p.suffix.lower()
        size = p.stat().st_size

        KNOWN_BINARY = {
            '.xlsx': 'Microsoft Excel (.xlsx)', '.xls': 'Microsoft Excel (.xls)',
            '.pdf': 'PDF Document', '.docx': 'Word Document (.docx)',
            '.doc': 'Word Document (.doc)', '.pptx': 'PowerPoint (.pptx)',
            '.png': 'PNG Image', '.jpg': 'JPEG Image', '.jpeg': 'JPEG Image',
            '.gif': 'GIF Image', '.bmp': 'Bitmap Image', '.webp': 'WebP Image',
            '.zip': 'ZIP Archive', '.7z': '7-Zip Archive', '.rar': 'RAR Archive',
            '.db': 'SQLite Database', '.sqlite': 'SQLite Database',
            '.pyc': 'Python Bytecode', '.pyd': 'Python Extension',
            '.dll': 'Dynamic Link Library', '.exe': 'Executable',
            '.so': 'Shared Object', '.pkl': 'Pickle File',
        }
        TEXT_EXTENSIONS = {
            '.csv', '.tsv', '.txt', '.md', '.json', '.xml', '.yaml', '.yml',
            '.toml', '.ini', '.cfg', '.log', '.html', '.css', '.js', '.ts',
            '.py', '.java', '.go', '.rs', '.c', '.cpp', '.h', '.sh', '.bat',
            '.ps1', '.sql', '.r', '.rb', '.php', '.swift', '.kt', '.scala',
        }

        if ext in TEXT_EXTENSIONS:
            type_desc = f"Text file ({ext}), but not valid UTF-8. Try encoding='gbk'."
        elif ext in KNOWN_BINARY:
            type_desc = KNOWN_BINARY[ext]
        else:
            import mimetypes
            mime_type, _ = mimetypes.guess_type(str(p))
            type_desc = mime_type or f"Binary file ({ext or 'no extension'})"

        return (
            f"[Binary file] {p.name}\n"
            f"Type: {type_desc}\n"
            f"Size: {size:,} bytes\n"
            f"Tip: This file cannot be read as text. Use copy_file to stage it into the "
            f"workspace, then process with execute_python."
        )
    except Exception as exc:
        return f"Read failed: {exc}"


@tool(
    name="write_file",
    description="在当前会话工作区中创建文件，或整体覆盖写入文件内容。覆盖已有文件前必须先 read_file 读取。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "文件内容"},
        },
        "required": ["path", "content"],
    },
    permission_level="write",
)
def write_file(path: str, content: str) -> str:
    sandbox = get_sandbox_manager()
    try:
        state, host_path, sandbox_path, _ = _prepare_mutable_path(
            sandbox,
            path,
            tool_name="write_file",
            arg_name="path",
            capability=PathCapability.WRITE,
        )
        existed = host_path.exists()
        if existed and not host_path.is_file():
            return f"Error: path is not a file: {path}"

        # Enforce read-before-write for existing files
        if existed and not _was_read(path):
            return (
                f"Write rejected: file '{path}' already exists and has not been read in this session. "
                "Please read the file first with read_file before overwriting it."
            )

        old_text = host_path.read_text(encoding="utf-8") if existed else ""
        sandbox_path.parent.mkdir(parents=True, exist_ok=True)
        sandbox_path.write_text(content, encoding="utf-8")
        sync_result = sandbox.sync_back(state)
        if sync_result.conflicts:
            return f"Write failed: {sync_result.to_summary()}"

        action = "updated" if existed else "created"
        diff_block = ""
        if existed and old_text != content:
            diff_block = f"\n[diff]\n{_build_diff(path, old_text, content)}"

        _record_read(path)
        return f"File {action}: {path} ({len(content)} chars){diff_block}\n{sync_result.to_summary()}"
    except ValueError as exc:
        return f"Path rejected: {exc}"
    except Exception as exc:
        return f"Write failed: {exc}"


@tool(
    name="edit_file",
    description="在文件中替换一段唯一匹配的文本，只适合精确修改单处内容。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "old_string": {"type": "string", "description": "需要被替换的原文本，必须唯一匹配"},
            "new_string": {"type": "string", "description": "替换后的新文本"},
        },
        "required": ["path", "old_string", "new_string"],
    },
    permission_level="write",
)
def edit_file(path: str, old_string: str, new_string: str) -> str:
    sandbox = get_sandbox_manager()
    try:
        state, host_path, sandbox_path, _ = _prepare_mutable_path(
            sandbox,
            path,
            tool_name="edit_file",
            arg_name="path",
            capability=PathCapability.WRITE,
        )
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

    # --- Match phase: exact first, then fuzzy fallback ---
    exact_match, count = _fuzzy_find_in_content(content, old_string)

    if count == 0:
        return (
            "Edit failed: old_string was not found in the file (even after whitespace normalization). "
            "Please re-read the file and copy the exact text you want to replace."
        )
    if count > 1:
        matches = []
        for idx, line in enumerate(content.splitlines(), 1):
            if old_string in line:
                matches.append(f"  line {idx}: {line.strip()[:120]}")
        hint = (
            ". Tip: provide more surrounding context to make your match unique"
            if len(matches) > 10
            else ""
        )
        return (
            f"Edit failed: old_string matched {count} times and must be unique.\n"
            + "Matches:\n"
            + "\n".join(matches[:10])
            + hint
        )

    # --- Replace phase ---
    used_fuzzy = old_string != exact_match
    try:
        new_content = content.replace(exact_match, new_string, 1)
        sandbox_path.write_text(new_content, encoding="utf-8")
        sync_result = sandbox.sync_back(state)
        if sync_result.conflicts:
            return f"Edit failed: {sync_result.to_summary()}"

        fuzzy_note = " (whitespace-normalized match)" if used_fuzzy else ""
        diff_block = _build_diff(path, content, new_content)
        _record_read(path)
        return (
            f"File edited{fuzzy_note}: {path}\n"
            f"[diff]\n{diff_block}\n"
            f"{sync_result.to_summary()}"
        )
    except Exception as exc:
        return f"Write failed: {exc}"


@tool(
    name="grep",
    description="按正则表达式搜索文件内容。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式模式"},
            "path": {"type": "string", "description": "搜索根目录，默认当前工作区"},
            "glob": {"type": "string", "description": "可选文件过滤模式，例如 *.py"},
            "context_lines": {"type": "integer", "description": "匹配行前后保留的上下文行数"},
            "case_sensitive": {"type": "boolean", "description": "是否区分大小写"},
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
    search_dir = resolve_readonly(path)
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
        return f"No matches found for '{pattern}'." if result.returncode == 1 else f"grep failed: {result.stderr.strip()}"
    return _format_grep_output(output)


def _grep_python(pattern: str, search_dir: Path, glob: str, context_lines: int, case_sensitive: bool) -> str:
    try:
        regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        return f"Invalid regex: {exc}"

    files = []
    for candidate in search_dir.rglob("*"):
        if not candidate.is_file():
            continue
        if glob:
            rel = str(candidate.relative_to(search_dir))
            if not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(candidate.name, glob)):
                continue
        files.append(candidate)

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
    description="按 glob 模式递归查找文件或目录。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式，例如 **/*.py"},
            "path": {"type": "string", "description": "搜索根目录，默认当前工作区"},
        },
        "required": ["pattern"],
    },
    permission_level="read",
)
def glob(pattern: str, path: str = ".") -> str:
    search_dir = resolve_readonly(path)
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
    description="移动或重命名单个文件或目录路径。",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "源路径"},
            "destination": {"type": "string", "description": "目标路径"},
        },
        "required": ["source", "destination"],
    },
    permission_level="write",
)
def move_file(source: str, destination: str) -> str:
    sandbox = get_sandbox_manager()
    try:
        state, source_host, source_out, _ = _prepare_mutable_path(
            sandbox,
            source,
            tool_name="move_file",
            arg_name="source",
            capability=PathCapability.MOVE,
        )
        _, dest_host, dest_out, _ = _prepare_mutable_path(
            sandbox,
            destination,
            tool_name="move_file",
            arg_name="destination",
            capability=PathCapability.MOVE,
        )
    except ValueError as exc:
        return f"Path rejected: {exc}"

    if not source_host.exists():
        return f"Error: source path does not exist: {source}"

    try:
        source_out.parent.mkdir(parents=True, exist_ok=True)
        dest_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_out), str(dest_out))
        sync_result = sandbox.sync_back(state)
        workspace = get_session_workspace()
        external_move = False
        if workspace is not None:
            workspace = workspace.resolve()
            external_move = not source_host.resolve().is_relative_to(workspace) or not dest_host.resolve().is_relative_to(workspace)
        moved_label = "Moved external path explicitly approved" if external_move else "Moved"
        return f"{moved_label}: {source} -> {destination}\n{sync_result.to_summary()}"
    except Exception as exc:
        return f"Move failed: {exc}"


@tool(
    name="copy_file",
    description="复制单个文件或目录到目标位置。",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "源路径"},
            "destination": {"type": "string", "description": "目标路径"},
        },
        "required": ["source", "destination"],
    },
    permission_level="write",
)
def copy_file(source: str, destination: str) -> str:
    sandbox = get_sandbox_manager()
    try:
        source_state, source_host, source_out, _ = _prepare_mutable_path(
            sandbox,
            source,
            tool_name="copy_file",
            arg_name="source",
            capability=PathCapability.READ,
        )
        dest_state, dest_host, dest_out, _ = _prepare_mutable_path(
            sandbox,
            destination,
            tool_name="copy_file",
            arg_name="destination",
            capability=PathCapability.WRITE,
        )
    except ValueError as exc:
        return f"Path rejected: {exc}"

    if not source_host.exists():
        return f"Error: source path does not exist: {source}"

    try:
        dest_out.parent.mkdir(parents=True, exist_ok=True)
        if source_out.is_dir():
            shutil.copytree(str(source_out), str(dest_out), dirs_exist_ok=True)
        else:
            shutil.copy2(str(source_out), str(dest_out))

        sync_result = sandbox.sync_back(dest_state)
        workspace = get_session_workspace()
        external_copy = False
        if workspace is not None:
            workspace = workspace.resolve()
            external_copy = not dest_host.resolve().is_relative_to(workspace)
        copied_label = "Copied external path explicitly approved" if external_copy else "Copied"
        return f"{copied_label}: {source} -> {destination}\n{sync_result.to_summary()}"
    except Exception as exc:
        return f"Copy failed: {exc}"


@tool(
    name="move_paths",
    description=(
        "当用户说的是一组已经明确知道的文件或目录时，按精确路径批量移动到目标目录。"
        "必须逐项传入完整路径列表，只能传封闭集合。例如：`paths=[\"1.txt\", \"2.txt\", ..., \"10.txt\"]`。"
        "不要传通配符、glob、pattern，也不要传区间或缩写形式，例如 `1*.txt`、`*.txt`、`1-10.txt` 都不允许。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "pattern": r"^[^*?\[]+$",
                    "description": (
                        "单个明确列出的源路径。"
                        "例如：`1.txt`、`2.txt`、`C:\\Users\\name\\Desktop\\10.txt`。"
                        "不要使用 `*`、`?`、`[ ]`、glob 语法，"
                        "也不要使用 `1-10.txt` 这类范围缩写。"
                    ),
                },
                "description": (
                    "这里只能传封闭集合的精确路径。每个文件都必须逐项列出。"
                    "如果用户指的是 10 个已知文件，就传 10 个精确路径，"
                    "不要传 `1*.txt` 或 `1-10.txt`。"
                    "示例：`paths=[\"1.txt\", \"2.txt\", ..., \"10.txt\"]`。"
                ),
            },
            "destination": {
                "type": "string",
                "description": "精确路径集合的目标目录。这里应传目录，而不是文件名模式。",
            },
        },
        "required": ["paths", "destination"],
    },
    permission_level="write",
)
def move_paths(paths: list[str], destination: str) -> str:
    exact_paths, error = _normalize_exact_paths(paths)
    if error:
        return error

    try:
        destination_root, destination_managed = resolve_with_capability(
            destination,
            tool_name="move_paths",
            arg_name="destination",
            capability=PathCapability.MOVE,
        )
    except ValueError as exc:
        return f"Path rejected: {exc}"

    if destination_root.exists() and not destination_root.is_dir():
        return f"Move failed: destination is not a directory: {destination}"

    sandbox = get_sandbox_manager()
    managed_state = None
    planned_targets: set[Path] = set()
    moved = 0

    try:
        if destination_managed:
            managed_state, _, destination_output_dir, _ = _prepare_mutable_path(
                sandbox,
                destination,
                tool_name="move_paths",
                arg_name="destination",
                capability=PathCapability.MOVE,
            )
        else:
            managed_state, _, destination_output_dir, _ = sandbox.external_writable_path(
                str(destination_root), profile="edit"
            )
        destination_output_dir.mkdir(parents=True, exist_ok=True)

        for raw_path in exact_paths:
            try:
                source_state, source_path, source_out, _ = _prepare_mutable_path(
                    sandbox,
                    raw_path,
                    tool_name="move_paths",
                    arg_name="paths",
                    capability=PathCapability.MOVE,
                )
            except ValueError as exc:
                return f"Path rejected: {exc}"

            managed_state = managed_state or source_state
            if not source_path.exists():
                return f"Error: source path does not exist: {raw_path}"

            target = destination_output_dir / source_path.name
            if target.exists():
                return f"Move failed: destination already exists: {target}"

            resolved_target = target.resolve()
            if resolved_target in planned_targets:
                return f"Move failed: multiple sources would map to the same destination: {target.name}"
            planned_targets.add(resolved_target)

            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_out), str(target))
            moved += 1

        sync_result = sandbox.sync_back(managed_state)
        if sync_result.conflicts:
            return f"Move failed: {sync_result.to_summary()}"
        return f"Moved {moved} paths to {destination_root}\n{sync_result.to_summary()}"
    except Exception as exc:
        return f"Move failed: {exc}"


@tool(
    name="copy_paths",
    description=(
        "当用户说的是一组已经明确知道的文件或目录时，按精确路径批量复制到目标目录。"
        "必须逐项传入完整路径列表，只能传封闭集合。"
        "不要传通配符、glob、pattern，也不要传区间或缩写形式，例如 `1*.txt`、`*.txt`、`1-10.txt` 都不允许。"
        "例如，如果用户说'复制 1 到 10 这十个文件到 backup'，应传 `paths=[\"1.txt\", \"2.txt\", ..., \"10.txt\"]`。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "pattern": r"^[^*?\[]+$",
                    "description": (
                        "单个明确列出的源路径。"
                        "例如：`1.txt`、`2.txt`、`C:\\Users\\name\\Desktop\\10.txt`。"
                        "不要使用 `*`、`?`、`[ ]`、glob 语法，"
                        "也不要使用 `1-10.txt` 这类范围缩写。"
                    ),
                },
                "description": (
                    "这里只能传封闭集合的精确路径。每个文件都必须逐项列出。"
                    "示例：`paths=[\"1.txt\", \"2.txt\", ..., \"10.txt\"]`。"
                ),
            },
            "destination": {
                "type": "string",
                "description": "目标目录路径。",
            },
        },
        "required": ["paths", "destination"],
    },
    permission_level="write",
)
def copy_paths(paths: list[str], destination: str) -> str:
    exact_paths, error = _normalize_exact_paths(paths)
    if error:
        return error

    try:
        destination_root, destination_managed = resolve_with_capability(
            destination,
            tool_name="copy_paths",
            arg_name="destination",
            capability=PathCapability.WRITE,
        )
    except ValueError as exc:
        return f"Path rejected: {exc}"

    if destination_root.exists() and not destination_root.is_dir():
        return f"Copy failed: destination is not a directory: {destination}"

    sandbox = get_sandbox_manager()
    managed_state = None
    planned_targets: set[Path] = set()
    copied = 0

    try:
        if destination_managed:
            managed_state, _, destination_output_dir, _ = _prepare_mutable_path(
                sandbox,
                destination,
                tool_name="copy_paths",
                arg_name="destination",
                capability=PathCapability.WRITE,
            )
        else:
            managed_state, _, destination_output_dir, _ = sandbox.external_writable_path(
                str(destination_root), profile="edit"
            )
        destination_output_dir.mkdir(parents=True, exist_ok=True)

        for raw_path in exact_paths:
            try:
                source_state, source_path, source_out, _ = _prepare_mutable_path(
                    sandbox,
                    raw_path,
                    tool_name="copy_paths",
                    arg_name="paths",
                    capability=PathCapability.READ,
                )
            except ValueError as exc:
                return f"Path rejected: {exc}"

            managed_state = managed_state or source_state
            if not source_path.exists():
                return f"Error: source path does not exist: {raw_path}"

            target = destination_output_dir / source_path.name
            if target.exists():
                return f"Copy failed: destination already exists: {target}"

            resolved_target = target.resolve()
            if resolved_target in planned_targets:
                return f"Copy failed: multiple sources would map to the same destination: {target.name}"
            planned_targets.add(resolved_target)

            target.parent.mkdir(parents=True, exist_ok=True)
            if source_out.is_dir():
                shutil.copytree(str(source_out), str(target), dirs_exist_ok=True)
            else:
                shutil.copy2(str(source_out), str(target))
            copied += 1

        sync_result = sandbox.sync_back(managed_state)
        if sync_result.conflicts:
            return f"Copy failed: {sync_result.to_summary()}"
        return f"Copied {copied} paths to {destination_root}\n{sync_result.to_summary()}"
    except Exception as exc:
        return f"Copy failed: {exc}"


@tool(
    name="delete_file",
    description="删除单个文件或目录（支持非空目录）。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要删除的路径"},
        },
        "required": ["path"],
    },
    permission_level="destroy",
)
def delete_file(path: str) -> str:
    try:
        sandbox = get_sandbox_manager()
        state, host_path, sandbox_path, _ = _prepare_mutable_path(
            sandbox,
            path,
            tool_name="delete_file",
            arg_name="path",
            capability=PathCapability.DELETE,
        )
    except ValueError as exc:
        return f"Path rejected: {exc}"

    if not host_path.exists():
        return f"Error: path does not exist: {path}"

    try:
        if sandbox_path.exists():
            if sandbox_path.is_dir():
                shutil.rmtree(sandbox_path, ignore_errors=False)
            else:
                sandbox_path.unlink()
        sync_result = sandbox.sync_back(state)
        deleted_label = "Deleted external path" if not host_path.is_relative_to(get_session_workspace() or host_path) else "Deleted path"
        return f"{deleted_label}: {path}\n{sync_result.to_summary()}"
    except Exception as exc:
        return f"Delete failed: {exc}"


@tool(
    name="delete_paths",
    description=(
        "当用户说的是一组已经明确知道的文件或目录时，按精确路径批量删除它们。"
        "必须逐项传入完整路径列表，只能传封闭集合。"
        "不要传通配符、glob、pattern，也不要传区间或缩写形式，例如 `1*.txt`、`*.txt`、`1-10.txt` 都不允许。"
        "例如，如果用户说“删除 1 到 10 这十个文件”，应传 `paths=[\"1.txt\", \"2.txt\", ..., \"10.txt\"]`。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "pattern": r"^[^*?\[]+$",
                    "description": (
                        "单个明确列出的待删除路径。"
                        "例如：`1.txt`、`2.txt`、`C:\\Users\\name\\Desktop\\10.txt`。"
                        "不要使用 `*`、`?`、`[ ]`、glob 语法，"
                        "也不要使用 `1-10.txt` 这类范围缩写。"
                    ),
                },
                "description": (
                    "这里只能传封闭集合的精确路径。每个文件都必须逐项列出。"
                    "如果用户指的是 10 个已知文件，就传 10 个精确路径，"
                    "不要传 `1*.txt` 或 `1-10.txt`。"
                    "示例：`paths=[\"1.txt\", \"2.txt\", ..., \"10.txt\"]`。"
                ),
            },
        },
        "required": ["paths"],
    },
    permission_level="destroy",
)
def delete_paths(paths: list[str]) -> str:
    exact_paths, error = _normalize_exact_paths(paths)
    if error:
        return error

    sandbox = get_sandbox_manager()
    managed_state = None
    resolved_items: list[tuple[Path, Path, str]] = []

    for raw_path in exact_paths:
        try:
            state, resolved_path, sandbox_path, _ = _prepare_mutable_path(
                sandbox,
                raw_path,
                tool_name="delete_paths",
                arg_name="paths",
                capability=PathCapability.DELETE,
            )
        except ValueError as exc:
            return f"Path rejected: {exc}"

        if not resolved_path.exists():
            return f"Error: path does not exist: {raw_path}"
        managed_state = managed_state or state
        resolved_items.append((resolved_path, sandbox_path, raw_path))

    resolved_items.sort(key=lambda item: (len(item[0].parts), str(item[0])), reverse=True)

    deleted = 0
    try:
        for _, sandbox_path, _ in resolved_items:
            if sandbox_path.exists():
                if sandbox_path.is_dir():
                    shutil.rmtree(sandbox_path, ignore_errors=False)
                else:
                    sandbox_path.unlink()
            deleted += 1

        sync_result = sandbox.sync_back(managed_state)
        if sync_result.conflicts:
            return f"Delete failed: {sync_result.to_summary()}"
        return f"Deleted {deleted} paths\n{sync_result.to_summary()}"
    except OSError as exc:
        return f"Delete failed (directory may be non-empty or permission denied): {exc}"
    except Exception as exc:
        return f"Delete failed: {exc}"


@tool(
    name="list_files",
    description="列出某个目录下的文件或子目录。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径"},
            "pattern": {"type": "string", "description": "可选的 glob 过滤模式"},
        },
        "required": ["path"],
    },
    permission_level="read",
)
def list_files(path: str, pattern: str = "*") -> str:
    p = resolve_readonly(path)
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


def _normalize_exact_paths(paths: list[str] | tuple[str, ...] | None) -> tuple[list[str], str | None]:
    if not isinstance(paths, (list, tuple)) or not paths:
        return [], "Error: paths must be a non-empty array of exact paths."

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            return [], "Error: each path must be a non-empty string."
        if any(token in raw for token in ("*", "?", "[")):
            return [], "Error: exact path tools do not accept wildcard patterns. Use glob or list_files to discover matching files, then pass the exact paths to this tool."
        stripped = raw.strip()
        if stripped in seen:
            continue
        seen.add(stripped)
        normalized.append(stripped)
    return normalized, None
