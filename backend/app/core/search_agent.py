"""SearchSubAgent — ReactAgent-based sub-agent for web research.

Searches the web, compiles findings, and presents results with source attribution.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from langchain_core.tools import StructuredTool

from app.core.agent import ReactAgent

logger = logging.getLogger(__name__)

SEARCH_SUBAGENT_SYSTEM_PROMPT = """你是一位信息检索专家。用户委托你搜索互联网并整理答案。

## 工作方法

### 1. 拆解问题
- 把用户问题拆成 2-3 个搜索关键词，覆盖不同角度
- 先用中文搜索，必要时用英文补充

### 2. 综合整理
- 对比多个来源，交叉验证关键信息
- 优先采信官方文档、权威来源
- 如果有矛盾信息，如实说明

### 3. 输出格式（Markdown）
- ## 核心结论（先给摘要）
- ## 详细发现（按主题组织）
- ## 信息来源（带链接列表）
- 引用来源时用 `[标题](URL)` 格式

## 可用工具
- search_web：搜索互联网，每次返回多条结果
- read_file / grep / glob：查看本地文件（按需）
"""


class SearchSubAgent:
    """A sub-agent for web research using ReactAgent."""

    def __init__(
        self,
        tools: list[StructuredTool],
        llm=None,
        max_rounds: int = 8,
        on_progress: Callable[[str], None] | None = None,
    ):
        self.tools = tools
        self.llm = llm
        self.max_rounds = max_rounds
        self.on_progress = on_progress or (lambda msg: None)

    async def execute(self, question: str) -> str:
        """Run the search and return compiled findings."""
        self.on_progress("开始搜索相关信息...")

        full_task = f"""## 搜索任务

{question}

请拆解问题、搜索多个关键词、综合整理后输出报告。"""

        agent = ReactAgent(
            tools=self.tools,
            llm=self.llm,
            system_prompt=SEARCH_SUBAGENT_SYSTEM_PROMPT,
            max_rounds=self.max_rounds,
        )

        try:
            result = await agent.call(full_task)
        except Exception as e:
            logger.exception("Search sub-agent failed")
            return f"搜索任务出错：{e}"

        self.on_progress("搜索完成")
        return result
