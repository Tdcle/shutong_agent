"""File operation tools — migrated from Java FileReaderTool."""

from __future__ import annotations

from pathlib import Path

from app.tools.base import tool


@tool(
    name="read_file",
    description="读取指定路径的文件内容",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "encoding": {"type": "string", "description": "文件编码，默认utf-8"},
        },
        "required": ["path"],
    },
)
def read_file(path: str, encoding: str = "utf-8") -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"错误: 文件不存在: {path}"
    if not p.is_file():
        return f"错误: 路径不是文件: {path}"
    try:
        return p.read_text(encoding=encoding)
    except Exception as e:
        return f"读取文件失败: {e}"


@tool(
    name="write_file",
    description="写入内容到指定路径的文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "要写入的内容"},
        },
        "required": ["path", "content"],
    },
)
def write_file(path: str, content: str) -> str:
    p = Path(path).expanduser().resolve()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"文件已写入: {path} ({len(content)} 字符)"
    except Exception as e:
        return f"写入文件失败: {e}"


@tool(
    name="list_files",
    description="列出目录中的文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径"},
            "pattern": {"type": "string", "description": "文件名匹配模式，如 *.py"},
        },
        "required": ["path"],
    },
)
def list_files(path: str, pattern: str = "*") -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"错误: 目录不存在: {path}"
    if not p.is_dir():
        return f"错误: 不是目录: {path}"
    files = sorted(p.glob(pattern))
    if not files:
        return f"目录 {path} 中没有匹配 '{pattern}' 的文件。"
    lines = []
    for f in files[:50]:
        suffix = "/" if f.is_dir() else ""
        lines.append(f"  {f.name}{suffix}")
    result = "\n".join(lines)
    if len(files) > 50:
        result += f"\n  ... 共 {len(files)} 个文件，仅显示前50个"
    return result
