"""AgentRouter — keyword fast-path + LLM fallback for intent-to-agent routing."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings, create_llm

if TYPE_CHECKING:
    from app.core.agent_registry import AgentRegistry

logger = logging.getLogger(__name__)

ROUTER_SYSTEM_PROMPT = """你是一个智能路由助手。根据用户消息判断最适合使用哪个 Agent。

只回复 agent 名称，不要解释。如果无法判断或用户没有明确指向特定能力，回复 "react"。
"""


class AgentRouter:
    def __init__(self, registry: "AgentRegistry", llm: ChatOpenAI | None = None):
        self._registry = registry
        self._llm = llm

    def _get_llm(self) -> ChatOpenAI:
        if self._llm is not None:
            return self._llm
        return create_llm(temperature=0.0, max_tokens=50)

    def _keyword_match(self, question: str) -> str | None:
        lower_q = question.lower()
        scores: dict[str, int] = {}

        for agent_def in self._registry.list_all():
            if not agent_def.keywords:
                scores[agent_def.name] = 0
                continue
            count = sum(1 for kw in agent_def.keywords if kw.lower() in lower_q)
            scores[agent_def.name] = count

        scored = sorted(
            [(name, score) for name, score in scores.items() if score > 0],
            key=lambda x: x[1],
            reverse=True,
        )

        if not scored:
            return None

        if len(scored) == 1:
            return scored[0][0]

        first_score = scored[0][1]
        second_score = scored[1][1]
        if first_score >= second_score * 2:
            return scored[0][0]

        return None

    async def route(self, question: str) -> str:
        keyword_result = self._keyword_match(question)
        if keyword_result is not None:
            logger.info("Router keyword-match: %s -> %s", question[:80], keyword_result)
            return keyword_result

        logger.info("Router keyword ambiguous, falling back to LLM routing for: %s", question[:80])
        try:
            llm = self._get_llm()

            agents_desc = "\n".join(
                f"- {ad.name}: {ad.description}" for ad in self._registry.list_all()
            )

            messages = [
                SystemMessage(content=ROUTER_SYSTEM_PROMPT),
                HumanMessage(content=f"""可用的 Agent：
{agents_desc}

用户消息：{question}"""),
            ]
            response = await llm.ainvoke(messages)
            agent_name = str(response.content).strip().lower()

            if self._registry.get(agent_name):
                logger.info("Router LLM-match: %s -> %s", question[:80], agent_name)
                return agent_name

            logger.warning("Router LLM returned unknown agent '%s', falling back to react", agent_name)
            return "react"
        except Exception as e:
            logger.warning("Router LLM call failed: %s, falling back to react", e)
            return "react"

    def route_sync(self, question: str) -> str:
        keyword_result = self._keyword_match(question)
        return keyword_result or "react"
