"""start_deep_analysis tool — delegates a deep analysis task to a ReflectionAgent sub-agent.

The sub-agent has access to search_web and read-only file tools. Progress is
streamed back via a ContextVar callback set by the SSE event generator.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.tools.base import tool

logger = logging.getLogger(__name__)

# ContextVar for progress callback — set by the SSE generator before tool execution
_deep_analysis_progress_cb: contextvars.ContextVar[Callable[[str], None] | None] = (
    contextvars.ContextVar("deep_analysis_progress", default=None)
)

# ContextVar for stashed document contents — set by chat.py when deep_analysis=true
_stashed_documents: contextvars.ContextVar[dict[str, str] | None] = (
    contextvars.ContextVar("stashed_documents", default=None)
)


def set_progress_callback(cb: Callable[[str], None] | None):
    _deep_analysis_progress_cb.set(cb)


def stash_documents(docs: dict[str, str] | None):
    _stashed_documents.set(docs)


@tool(
    name="start_deep_analysis",
    description=(
        "启动一个子 Agent 对指定主题或文档进行深度分析。"
        "子 Agent 会使用 search_web 搜索补充信息，经过多轮反思后生成详细报告。"
        "适用于论文解读、技术调研、文档深度分析等需要深入研究的场景。"
        "调用时需要提供分析问题（question），如果涉及已上传的文档可以用 document_names 指定。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "需要深度分析的具体问题，越明确越好。例如：'分析这篇论文的实验设计和方法论'",
            },
            "document_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "与任务关联的文档文件名列表（可选）。留空则仅基于搜索进行研究。",
            },
        },
        "required": ["question"],
    },
    permission_level="read",
)
async def start_deep_analysis(question: str, document_names: list[str] | None = None) -> str:
    """Spawn a deep analysis sub-agent and return the report."""
    from app.core.deep_analysis import DeepAnalysisSubAgent
    from app.config import create_llm
    from app.tools.search import search_web
    from app.tools.document_ops import read_document as read_doc_fn
    from app.tools.file_ops import read_file as read_file_fn, grep as grep_fn, glob as glob_fn
    from langchain_core.tools import StructuredTool

    progress = _deep_analysis_progress_cb.get()

    # Build restricted tool set for sub-agent
    sub_tools: list[StructuredTool] = [
        StructuredTool.from_function(
            func=search_web,
            name="search_web",
            description="搜索互联网获取最新信息",
        ),
        StructuredTool.from_function(
            func=read_doc_fn,
            name="read_document",
            description="读取 PDF、Word、Excel 等文档内容",
        ),
        StructuredTool.from_function(
            func=read_file_fn,
            name="read_file",
            description="读取文件内容",
        ),
        StructuredTool.from_function(
            func=grep_fn,
            name="grep",
            description="搜索文件内容",
        ),
        StructuredTool.from_function(
            func=glob_fn,
            name="glob",
            description="按模式查找文件",
        ),
    ]

    # Load stashed document contents
    document_contents: dict[str, str] = {}
    stashed = _stashed_documents.get()
    if stashed and document_names:
        for name in document_names:
            # Match by filename
            if name in stashed:
                document_contents[name] = stashed[name]
            else:
                # Try partial match
                for key, content in stashed.items():
                    if name in key or Path(key).name == name:
                        document_contents[Path(key).name] = content
                        break

    if progress:
        if document_contents:
            progress(f"已匹配 {len(document_contents)} 个文档，启动深度分析...")
        else:
            progress("启动深度分析（基于搜索）...")

    agent = DeepAnalysisSubAgent(
        tools=sub_tools,
        llm=create_llm(temperature=0.3),
        max_reflection_rounds=3,
        on_progress=progress,
    )

    try:
        report = await agent.analyze(question, document_contents or None)
    except Exception as e:
        logger.exception("Deep analysis failed")
        return f"深度分析失败：{e}"

    return report
