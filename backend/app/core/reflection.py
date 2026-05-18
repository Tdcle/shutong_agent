"""Reflection Agent — migrated from Java ReflectionAgent + ReflectionAdvisor.

Wraps a ReAct agent with self-reflection: after the agent produces an answer,
an LLM critic reviews it and the agent revises if needed.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.core.agent import ReactAgent

logger = logging.getLogger(__name__)

REFLECTION_PROMPT = """你是一个质量评审专家。请审视以下回答，判断是否充分满足用户需求。

## 评审标准
1. 是否完整回答了用户问题
2. 信息是否准确、有依据
3. 逻辑是否清晰、结论是否可靠
4. 是否遗漏了关键信息

如果回答充分满足需求，回复 "PASS"。
如果不足，请给出具体改进建议，格式: "REVISE: <具体改进意见>"
"""


class ReflectionAgent:
    def __init__(
        self,
        tools: list[BaseTool],
        llm: ChatOpenAI | None = None,
        system_prompt: str = "",
        max_reflection_rounds: int = 2,
        max_react_rounds: int = 10,
        context_char_limit: int | None = None,
    ):
        self.llm = llm or self._default_llm()
        self.system_prompt = system_prompt
        self.max_reflection_rounds = max_reflection_rounds
        self.react_agent = ReactAgent(
            tools=tools,
            llm=self.llm,
            system_prompt=system_prompt,
            max_rounds=max_react_rounds,
            context_char_limit=context_char_limit or settings.context_char_limit,
        )

    @staticmethod
    def _default_llm() -> ChatOpenAI:
        from app.config import create_llm
        return create_llm()

    async def call(self, question: str, history: list | None = None) -> str:
        answer = await self.react_agent.call(question, history)

        for round_idx in range(self.max_reflection_rounds):
            review = await self.llm.ainvoke([
                SystemMessage(content=REFLECTION_PROMPT),
                HumanMessage(content=f"""## 用户问题
{question}

## AI回答
{answer}"""),
            ])

            review_text = str(review.content).strip()
            if review_text.upper().startswith("PASS"):
                logger.info("Reflection round %d: PASS", round_idx + 1)
                break

            # Extract revision suggestion
            suggestion = review_text.removeprefix("REVISE:").strip()
            logger.info("Reflection round %d: REVISE - %s", round_idx + 1, suggestion[:100])

            # Re-run with feedback
            answer = await self.react_agent.call(
                f"""请根据以下反馈改进你的回答。

## 原始问题
{question}

## 之前的回答
{answer}

## 改进意见
{suggestion}

请给出改进后的完整回答。""",
                history,
            )

        return answer

    async def stream(self, question: str, history: list | None = None) -> AsyncIterator[str]:
        """Stream the final answer after reflection loop."""
        answer = await self.call(question, history)
        yield answer
