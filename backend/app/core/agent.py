"""ReAct Agent — migrated from Java SimpleReactAgent.

Uses LangGraph's create_react_agent with added:
- Context compression (migrated from compressIfNeeded)
- Conversation memory integration
- Max rounds enforcement
- Streaming support
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

REACT_SYSTEM_PROMPT = """## 角色
你是一个严格遵循 ReAct 模式的智能 AI 助手，通过 Reasoning → Act(工具调用) → Observation 的反复循环来逐步完成任务。

## 工具调用规则
1. 如果需要调用工具，只通过工具调用字段输出，禁止在文本内容中出现工具调用格式。
2. 调用工具时参数必须是有效 JSON，参数必须简洁。
3. 不重复调用同一个工具（名称+参数完全一致），除非之前调用失败。

## 最终答案规则
1. 如果上下文已拥有完成任务所需的全部信息，则不要再调用任何工具。
2. 最终答案只允许自然语言，不能包含 JSON、思考过程或伪代码。

## 强制要求
1. 如果本轮没有工具调用，视为任务完成，直接输出最终答案。
2. 禁止输出会干扰工具系统解析的任何结构。
"""


class ReactAgent:
    """ReAct agent with conversation memory, context compression, and streaming."""

    def __init__(
        self,
        tools: list[BaseTool],
        llm: ChatOpenAI | None = None,
        system_prompt: str = "",
        max_rounds: int | None = None,
        context_char_limit: int | None = None,
    ):
        self.tools = {t.name: t for t in tools}
        self.tool_list = tools
        self.llm = llm or self._default_llm()
        self.system_prompt = system_prompt
        self.max_rounds = max_rounds or settings.max_agent_rounds
        self.context_char_limit = context_char_limit or settings.context_char_limit

    @staticmethod
    def _default_llm() -> ChatOpenAI:
        return ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )

    def _build_messages(self, question: str, history: list | None = None) -> list:
        msgs = [
            SystemMessage(content=REACT_SYSTEM_PROMPT),
            SystemMessage(content=self.system_prompt),
        ]
        if history:
            msgs.extend(history)
        msgs.append(HumanMessage(content=f"<question>\n{question}\n</question>"))
        return msgs

    async def call(self, question: str, history: list | None = None) -> str:
        """Non-streaming call."""
        llm_with_tools = self.llm.bind_tools(self.tool_list)
        messages = self._build_messages(question, history)
        round_count = 0

        while True:
            round_count += 1
            if self.max_rounds > 0 and round_count > self.max_rounds:
                messages.append(HumanMessage(content="已达到最大推理轮次。请基于当前已有信息直接给出最终答案，禁止再调用任何工具。"))
                resp = await self.llm.ainvoke(messages)
                return str(resp.content)

            resp = await llm_with_tools.ainvoke(messages)
            messages.append(resp)

            if not resp.tool_calls:
                return str(resp.content)

            # Execute tools
            for tc in resp.tool_calls:
                tool = self.tools.get(tc["name"])
                if tool is None:
                    result = f"错误: 未找到工具 '{tc['name']}'"
                else:
                    try:
                        result = await tool.ainvoke(tc["args"])
                    except Exception as e:
                        result = f"工具执行失败: {e}"
                messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

            # Context compression
            self._compress_if_needed(messages)

    async def stream(self, question: str, history: list | None = None) -> AsyncIterator[str]:
        """Streaming call with SSE-compatible token output."""
        llm_with_tools = self.llm.bind_tools(self.tool_list)
        messages = self._build_messages(question, history)
        round_count = 0

        while True:
            round_count += 1
            if self.max_rounds > 0 and round_count > self.max_rounds:
                messages.append(HumanMessage(content="已达到最大推理轮次。请基于当前已有信息直接给出最终答案，禁止再调用任何工具。"))
                async for chunk in self.llm.astream(messages):
                    if chunk.content:
                        yield str(chunk.content)
                return

            full_content = ""
            tool_calls_acc: list[dict] = []
            async for chunk in llm_with_tools.astream(messages):
                if chunk.content:
                    full_content += str(chunk.content)
                    yield str(chunk.content)
                if chunk.tool_calls:
                    for tc in chunk.tool_calls:
                        existing = next((t for t in tool_calls_acc if t.get("id") == tc.get("id")), None)
                        if existing:
                            existing["args"] = (existing.get("args", "") + str(tc.get("args", "")))
                        else:
                            tool_calls_acc.append(dict(tc))

            if not tool_calls_acc:
                return

            # Build assistant message with tool calls
            ai_msg = AIMessage(content=full_content or "", tool_calls=tool_calls_acc)
            messages.append(ai_msg)

            # Execute tools
            for tc_raw in tool_calls_acc:
                tool = self.tools.get(tc_raw["name"])
                if tool is None:
                    result = f"错误: 未找到工具 '{tc_raw['name']}'"
                else:
                    try:
                        result = await tool.ainvoke(tc_raw["args"])
                    except Exception as e:
                        result = f"工具执行失败: {e}"
                messages.append(ToolMessage(content=str(result), tool_call_id=tc_raw["id"]))

            self._compress_if_needed(messages)

    def _compress_if_needed(self, messages: list):
        """Context compression — migrated from Java compressIfNeeded."""
        total_chars = sum(len(str(m.content or "")) for m in messages)
        if total_chars <= self.context_char_limit:
            return
        logger.warning("Context too large (%d chars), compressing...", total_chars)

        # Keep system messages, compress the middle conversation
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        other_msgs = [m for m in messages if not isinstance(m, SystemMessage)]

        if len(other_msgs) <= 4:
            return  # Too few messages to compress meaningfully

        # Keep first user message and last 4 messages, summarize the rest
        keep_tail = other_msgs[-4:]
        to_compress = other_msgs[1:-4]
        if not to_compress:
            return

        compressed_text = "\n".join(f"[{m.__class__.__name__}]: {str(m.content)[:200]}..." for m in to_compress)
        summary_msg = SystemMessage(content=f"【压缩的历史上下文】\n{compressed_text}")

        messages.clear()
        messages.extend(system_msgs)
        messages.append(other_msgs[0])  # Original user question
        messages.append(summary_msg)
        messages.extend(keep_tail)
