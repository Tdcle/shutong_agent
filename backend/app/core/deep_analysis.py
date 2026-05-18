"""DeepAnalysisSubAgent — ReflectionAgent-based deep analysis with search capability.

Spawns as a sub-agent from the main ReactAgent. Uses only read-only tools
(search_web, read_document, read_file, grep, glob) and runs 3 reflection rounds.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langchain_core.tools import StructuredTool

from app.core.reflection import ReflectionAgent

logger = logging.getLogger(__name__)

DEEP_ANALYSIS_SYSTEM_PROMPT = """你是一位深度分析专家，负责对文档或主题进行深入研究并输出高质量的分析报告。

## 工作方式
- 首先仔细理解用户的问题，明确分析目标
- 如果有参考文档，优先从文档中提取相关信息
- 根据任务需要自主判断是否使用 search_web 搜索补充资料：
  - 总结/解读文档 → 通常不需要搜索，基于文档本身即可
  - 技术调研/行业分析 → 需要搜索补充最新信息
  - 需要佐证或查漏补缺 → 选择性搜索
- 整合所有信息后，审视你的分析是否充分。如有明显遗漏，可补充搜索
- 最终输出完整的分析报告

## 输出要求（重要）
必须使用 **Markdown** 格式输出，具体要求：
- 使用 # ## ### 层级标题组织内容
- 先给出 **核心结论**（摘要），再展开详细分析
- 使用列表、表格等结构化方式呈现信息
- 引用文档内容时标注「文档原文」，搜索结果引用时附链接
- 不确定的信息请明确说明
- 不要使用代码块包裹正文，代码块仅用于代码示例

## 可用工具
- search_web：搜索互联网信息（按需使用，非必需）
- read_document / read_file：阅读文档和文件
- grep / glob：在项目中搜索文件
- 你只有只读权限，不能修改任何文件
"""


class DeepAnalysisSubAgent:
    """A sub-agent that runs deep analysis using ReflectionAgent + search."""

    def __init__(
        self,
        tools: list[StructuredTool],
        llm=None,
        max_reflection_rounds: int = 3,
        on_progress: Callable[[str], None] | None = None,
    ):
        self.tools = tools
        self.llm = llm
        self.max_reflection_rounds = max_reflection_rounds
        self.on_progress = on_progress or (lambda msg: None)

    async def analyze(self, question: str, document_contents: dict[str, str] | None = None) -> str:
        """Run the deep analysis and return the report.

        Args:
            question: The user's analysis question.
            document_contents: Dict of {filename: content} for uploaded documents.
        """
        self.on_progress("正在准备分析任务...")

        # Build the full prompt with document content injected for the sub-agent
        prompt_parts = [DEEP_ANALYSIS_SYSTEM_PROMPT]

        if document_contents:
            doc_texts = []
            for filename, content in document_contents.items():
                doc_texts.append(f"## 参考文档：{filename}\n\n{content}")
            prompt_parts.append("\n\n---\n\n".join(doc_texts))
            self.on_progress(f"已加载 {len(document_contents)} 个参考文档")
        else:
            self.on_progress("无参考文档，将完全依赖搜索进行研究")

        full_system_prompt = "\n\n".join(prompt_parts)

        analysis_task = f"""## 深度分析任务

{question}

请按照工作流程进行深度分析，输出完整的分析报告。"""

        # Build ReflectionAgent with restricted tools and a larger context window
        # (documents can be 50k chars + search results + analysis)
        reflection_agent = ReflectionAgent(
            tools=self.tools,
            llm=self.llm,
            system_prompt=full_system_prompt,
            max_reflection_rounds=self.max_reflection_rounds,
            max_react_rounds=8,
            context_char_limit=60000,
        )

        # Override the internal ReAct agent's _default_llm to use the same llm
        if self.llm:
            reflection_agent.react_agent.llm = self.llm

        self.on_progress("开始搜索和研究...")
        try:
            report = await reflection_agent.call(analysis_task)
        except Exception as e:
            logger.exception("Deep analysis sub-agent failed")
            return f"深度分析过程出错：{e}"

        self.on_progress("分析完成，生成报告")
        return report
