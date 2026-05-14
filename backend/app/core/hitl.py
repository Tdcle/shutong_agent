"""HITL (Human-in-the-Loop) Agent — migrated from Java HITLReactAgent.

Supports interrupting tool execution for human approval via LangGraph's interrupt mechanism.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger(__name__)


class FeedbackResult(Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"


@dataclass
class PendingToolCall:
    id: str
    name: str
    arguments: str
    description: str = ""

    def approve(self) -> "PendingToolCall":
        self._result = FeedbackResult.APPROVED
        return self

    def reject(self, reason: str = "") -> "PendingToolCall":
        self._result = FeedbackResult.REJECTED
        self._reject_reason = reason
        return self

    @property
    def result(self) -> FeedbackResult:
        return getattr(self, "_result", None)


@dataclass
class HITLState:
    approved_tool_names: set[str] = field(default_factory=set)
    consumed_tool_ids: set[str] = field(default_factory=set)

    def is_consumed(self, tool_id: str) -> bool:
        return tool_id in self.consumed_tool_ids

    def mark_consumed(self, tool_id: str):
        self.consumed_tool_ids.add(tool_id)

    def mark_approved(self, tool_name: str):
        self.approved_tool_names.add(tool_name)

    def is_approved(self, tool_name: str) -> bool:
        return tool_name in self.approved_tool_names


class AgentInterrupted(Exception):
    def __init__(self, pending_tools: list[PendingToolCall], messages: list[BaseMessage], context: dict):
        self.pending_tools = pending_tools
        self.messages = messages
        self.context = context


class AgentFinished(Exception):
    def __init__(self, content: str):
        self.content = content


HITL_SYSTEM_PROMPT = """## 角色
你是一个严格遵循 ReAct 模式的智能 AI 助手。

## 工具调用规则
1. 需要使用工具时，只通过工具调用字段输出，禁止在文本中出现工具调用格式。
2. 某些工具调用需要人工审批，请耐心等待审批结果。
3. 不重复调用同一个工具，除非之前调用失败。

## 最终答案规则
如果上下文已拥有完成任务所需的全部信息，不要再调用任何工具，直接输出最终自然语言答案。
"""


class HITLAgent:
    def __init__(
        self,
        tools: list[BaseTool],
        intercept_tools: set[str] | None = None,
        llm: ChatOpenAI | None = None,
        max_rounds: int = 10,
    ):
        self.tools = {t.name: t for t in tools}
        self.tool_list = tools
        self.intercept_tools = intercept_tools or set()
        self.llm = llm or self._default_llm()
        self.max_rounds = max_rounds

    @staticmethod
    def _default_llm() -> ChatOpenAI:
        return ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )

    async def call(self, question: str) -> AgentFinished | AgentInterrupted:
        messages: list[BaseMessage] = [
            SystemMessage(content=HITL_SYSTEM_PROMPT),
            HumanMessage(content=question),
        ]
        hitl_state = HITLState()
        return await self._run(messages, hitl_state)

    async def resume(self, interrupted: AgentInterrupted, feedbacks: list[PendingToolCall]) -> AgentFinished | AgentInterrupted:
        messages = list(interrupted.messages)
        hitl_state = interrupted.context.get("hitl_state", HITLState())

        # Process feedback
        for fb in feedbacks:
            if hitl_state.is_consumed(fb.id):
                continue
            hitl_state.mark_consumed(fb.id)

            if fb.result == FeedbackResult.APPROVED:
                hitl_state.mark_approved(fb.name)

            if fb.result == FeedbackResult.REJECTED:
                reject_reason = getattr(fb, "_reject_reason", "用户拒绝执行此工具")
                messages.append(ToolMessage(
                    content=f"工具执行被拒绝: {reject_reason}",
                    tool_call_id=fb.id,
                ))
            else:
                tool = self.tools.get(fb.name)
                if tool:
                    try:
                        result = await tool.ainvoke(json.loads(fb.arguments) if isinstance(fb.arguments, str) else fb.arguments)
                    except Exception as e:
                        result = f"工具执行失败: {e}"
                    messages.append(ToolMessage(content=str(result), tool_call_id=fb.id))

        return await self._run(messages, hitl_state)

    async def _run(self, messages: list[BaseMessage], hitl_state: HITLState) -> AgentFinished | AgentInterrupted:
        llm_with_tools = self.llm.bind_tools(self.tool_list)
        round_count = 0

        while True:
            round_count += 1
            if self.max_rounds > 0 and round_count > self.max_rounds:
                resp = await self.llm.ainvoke(messages)
                return AgentFinished(content=str(resp.content))

            resp = await llm_with_tools.ainvoke(messages)
            messages.append(resp)

            if not resp.tool_calls:
                return AgentFinished(content=str(resp.content))

            # Separate intercept vs non-intercept tools
            intercept_calls = []
            non_intercept_calls = []
            for tc in resp.tool_calls:
                if tc["name"] in self.intercept_tools and not hitl_state.is_approved(tc["name"]):
                    intercept_calls.append(tc)
                else:
                    non_intercept_calls.append(tc)

            # Execute non-intercept tools immediately
            for tc in non_intercept_calls:
                tool = self.tools.get(tc["name"])
                if tool:
                    try:
                        result = await tool.ainvoke(tc["args"])
                    except Exception as e:
                        result = f"工具执行失败: {e}"
                    messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

            # If there are intercept tools, halt and ask human
            if intercept_calls:
                pending = [
                    PendingToolCall(
                        id=tc["id"],
                        name=tc["name"],
                        arguments=json.dumps(tc["args"], ensure_ascii=False),
                        description=tc.get("description", ""),
                    )
                    for tc in intercept_calls
                ]
                return AgentInterrupted(
                    pending_tools=pending,
                    messages=messages,
                    context={"hitl_state": hitl_state},
                )

    async def stream(self, question: str) -> AsyncIterator[str]:
        """Non-HITL streaming — uses standard ReAct streaming."""
        from app.core.agent import ReactAgent
        agent = ReactAgent(tools=self.tool_list, llm=self.llm, max_rounds=self.max_rounds)
        async for chunk in agent.stream(question):
            yield chunk
