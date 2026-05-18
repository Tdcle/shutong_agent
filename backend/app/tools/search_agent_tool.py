"""start_search_agent tool — delegates a web research task to a SearchSubAgent.

The sub-agent searches the web, compiles findings, and returns a report with sources.
"""

from __future__ import annotations

import logging

from langchain_core.tools import StructuredTool

from app.tools.base import tool
from app.tools.deep_analysis_tool import _deep_analysis_progress_cb

logger = logging.getLogger(__name__)


@tool(
    name="start_search_agent",
    description=(
        "启动搜索子 Agent 进行互联网调研。子 Agent 会拆解问题、搜索多个关键词、"
        "交叉验证信息、输出带来源链接的整理报告。适合需要查找最新资料、对比信息、"
        "了解陌生主题等场景。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "搜索主题或问题，越具体越好。如：'Python 3.14 的新特性有哪些'、'Flutter vs React Native 2025 对比'",
            },
        },
        "required": ["question"],
    },
    permission_level="read",
)
async def start_search_agent(question: str) -> str:
    """Spawn a search sub-agent and return compiled findings."""
    from app.core.search_agent import SearchSubAgent
    from app.config import create_llm
    from app.tools.search import search_web
    from app.tools.file_ops import read_file, grep, glob

    progress = _deep_analysis_progress_cb.get()

    sub_tools: list[StructuredTool] = [
        StructuredTool.from_function(func=search_web, name="search_web", description="搜索互联网获取最新信息"),
        StructuredTool.from_function(func=read_file, name="read_file", description="读取本地文件"),
        StructuredTool.from_function(func=grep, name="grep", description="搜索文件内容"),
        StructuredTool.from_function(func=glob, name="glob", description="按模式查找文件"),
    ]

    if progress:
        progress(f"启动搜索 Agent：{question[:80]}...")

    agent = SearchSubAgent(
        tools=sub_tools,
        llm=create_llm(temperature=0.3),
        max_rounds=8,
        on_progress=progress,
    )

    try:
        result = await agent.execute(question)
    except Exception as e:
        logger.exception("Search agent failed")
        return f"搜索任务失败：{e}"

    return result
