"""start_code_agent tool — delegates a coding task to a CodeSubAgent.

The sub-agent uses ReactAgent with code-specific discipline (read→edit→verify→report).
Progress is streamed back via the same ContextVar callback used by deep analysis.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Callable

from langchain_core.tools import StructuredTool

from app.tools.base import tool

logger = logging.getLogger(__name__)

# Reuse the same ContextVar from deep_analysis_tool for progress callback
from app.tools.deep_analysis_tool import _deep_analysis_progress_cb


@tool(
    name="start_code_agent",
    description=(
        "启动一个代码子 Agent 完成编程任务。子 Agent 会先阅读代码、再精确修改、最后验证。"
        "适用于：写脚本、重构、修 bug、生成项目代码、写测试等。"
        "传入具体的任务描述（task）和可选的工作目录（working_dir）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "编程任务的详细描述，如：'重构 backend/auth.py，将 Token 验证提取为独立函数'",
            },
            "working_dir": {
                "type": "string",
                "description": "工作目录，默认为当前项目根目录",
            },
        },
        "required": ["task"],
    },
    permission_level="write",
)
async def start_code_agent(task: str, working_dir: str = ".") -> str:
    """Spawn a code sub-agent and return the result."""
    from app.core.code_agent_sub import CodeSubAgent
    from app.config import create_llm
    from app.tools.file_ops import (
        read_file, write_file, edit_file, grep, glob, list_files,
    )
    from app.tools.shell import execute_python, execute_bash

    progress = _deep_analysis_progress_cb.get()

    # Build restricted tool set for code sub-agent
    sub_tools: list[StructuredTool] = [
        StructuredTool.from_function(func=read_file, name="read_file", description="读取文件内容"),
        StructuredTool.from_function(func=write_file, name="write_file", description="创建或覆盖文件"),
        StructuredTool.from_function(func=edit_file, name="edit_file", description="精确替换文件中的文本片段"),
        StructuredTool.from_function(func=grep, name="grep", description="按正则搜索文件内容"),
        StructuredTool.from_function(func=glob, name="glob", description="按模式查找文件"),
        StructuredTool.from_function(func=list_files, name="list_files", description="列出目录内容"),
        StructuredTool.from_function(
            coroutine=execute_python,
            name="execute_python",
            description="执行 Python 代码或脚本",
        ),
        StructuredTool.from_function(
            coroutine=execute_bash,
            name="execute_bash",
            description="运行 bash 命令、构建或测试",
        ),
    ]

    # Build tool_defs for permission level lookup
    from app.tools.base import ToolDef
    sub_tool_defs: dict[str, ToolDef] = {}
    for fn in [read_file, write_file, edit_file, grep, glob, list_files]:
        name = getattr(fn, "_tool_name", fn.__name__)
        sub_tool_defs[name] = ToolDef(
            name=name,
            description=getattr(fn, "_tool_description", ""),
            fn=fn,
            permission_level=getattr(fn, "_tool_permission_level", "read"),
        )
    sub_tool_defs["execute_python"] = ToolDef(
        name="execute_python", description="执行 Python 代码", fn=execute_python, permission_level="shell")
    sub_tool_defs["execute_bash"] = ToolDef(
        name="execute_bash", description="运行 bash 命令", fn=execute_bash, permission_level="shell")

    if progress:
        progress(f"启动代码 Agent：{task[:80]}...")

    agent = CodeSubAgent(
        tools=sub_tools,
        llm=create_llm(temperature=0.1),
        max_rounds=12,
        on_progress=progress,
        tool_defs=sub_tool_defs,
    )

    try:
        result = await agent.execute(task, working_dir)
    except Exception as e:
        logger.exception("Code agent failed")
        return f"代码任务失败：{e}"

    return result
