"""Plan-Execute Agent — migrated from Java PlanExecuteAgent.

Implements the Plan → Execute → Critique → Summarize loop.
Uses LangGraph StateGraph for state machine management.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.config import settings, create_llm

logger = logging.getLogger(__name__)


@dataclass
class PlanTask:
    id: str
    instruction: str
    order: int = 0


@dataclass
class CritiqueResult:
    passed: bool
    feedback: str


@dataclass
class TaskResult:
    task_id: str
    success: bool
    output: str | None = None
    error: str | None = None


@dataclass
class OverallState:
    question: str
    messages: list[BaseMessage] = field(default_factory=list)
    round: int = 0
    plan: list[PlanTask] = field(default_factory=list)
    task_results: dict[str, TaskResult] = field(default_factory=dict)

    def current_chars(self) -> int:
        return sum(len(str(m.content or "")) for m in self.messages)


PLAN_SYSTEM_PROMPT = """你是一个规划专家。根据用户问题和对话历史，生成执行计划。
计划是一系列有序的任务，每个任务有: id（唯一标识）、instruction（具体指令）、order（执行顺序）。
相同 order 的任务可以并行执行。若无可用工具或不需要执行，返回空列表。

输出格式（严格JSON）:
[{"id": "task_1", "instruction": "具体任务描述", "order": 1}, ...]
"""

EXECUTE_SYSTEM_PROMPT = """你是一个任务执行者。根据给定的前置结果和当前任务，调用合适的工具完成任务。
充分利用已有结果中的信息，不要重复获取。
"""

CRITIQUE_SYSTEM_PROMPT = """你是一个质量评审专家。根据任务执行情况判断目标是否已达成。

输出JSON格式:
{"passed": true/false, "feedback": "评审意见"}
"""

SUMMARIZE_SYSTEM_PROMPT = """你是一个报告撰写专家。根据用户原始问题和所有执行上下文，生成一份完整、专业的最终答案。
请包含所有关键信息，确保回答充分满足用户需求。
"""


class PlanExecuteAgent:
    def __init__(
        self,
        tools: list[BaseTool],
        llm: ChatOpenAI | None = None,
        max_rounds: int = 3,
        max_tool_retries: int = 2,
        context_char_limit: int | None = None,
        tool_concurrency: int = 3,
    ):
        self.tools = {t.name: t for t in tools}
        self.tool_list = tools
        self.llm = llm or self._default_llm()
        self.max_rounds = max_rounds
        self.max_tool_retries = max_tool_retries
        self.context_char_limit = context_char_limit or settings.context_char_limit
        self.tool_semaphore = asyncio.Semaphore(tool_concurrency)

    @staticmethod
    def _default_llm() -> ChatOpenAI:
        return create_llm(temperature=0.3)

    def _render_messages(self, messages: list[BaseMessage]) -> str:
        parts = []
        for m in messages:
            role = m.__class__.__name__.replace("Message", "")
            parts.append(f"[{role}]\n{m.content}")
        return "\n\n".join(parts)

    def _tool_description(self) -> str:
        if not self.tool_list:
            return "（当前无可用工具）"
        return "\n".join(f"- {t.name}: {t.description}" for t in self.tool_list)

    async def _generate_plan(self, state: OverallState) -> list[PlanTask]:
        llm = self.llm.bind(response_format={"type": "json_object"})
        prompt = HumanMessage(content=f"""当前是第 {state.round} 轮迭代。

## 可用工具
{self._tool_description()}

## 对话历史
{self._render_messages(state.messages)}

请生成执行计划（JSON数组格式）。""")

        resp = await llm.ainvoke([SystemMessage(content=PLAN_SYSTEM_PROMPT), prompt])
        text = str(resp.content).strip()

        # Extract JSON array from response
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                data = data.get("plan", data.get("tasks", []))
            if not isinstance(data, list):
                data = []
        except json.JSONDecodeError:
            # Try to find JSON array in text
            import re
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    data = []
            else:
                data = []

        return [PlanTask(id=t.get("id", f"task_{i}"), instruction=t.get("instruction", ""), order=t.get("order", 0))
                for i, t in enumerate(data)]

    async def _execute_plan(self, state: OverallState) -> dict[str, TaskResult]:
        results: dict[str, TaskResult] = {}
        accumulated: dict[str, str] = {}

        # Group by order
        by_order: dict[int, list[PlanTask]] = {}
        for t in state.plan:
            by_order.setdefault(t.order, []).append(t)

        llm = self.llm.bind_tools(self.tool_list)

        for order in sorted(by_order):
            tasks = by_order[order]
            dep_snapshot = "\n".join(f"{tid}: {out}" for tid, out in accumulated.items())

            async def execute_one(task: PlanTask) -> None:
                async with self.tool_semaphore:
                    result = await self._execute_with_retry(task, dep_snapshot, llm)
                    results[task.id] = result
                    if result.success and result.output:
                        accumulated[task.id] = result.output

            await asyncio.gather(*[execute_one(t) for t in tasks])

        return results

    async def _execute_with_retry(self, task: PlanTask, dep_snapshot: str, llm) -> TaskResult:
        last_error = None
        for attempt in range(self.max_tool_retries):
            try:
                prompt_text = f"""【前置任务结果】
{dep_snapshot if dep_snapshot else "无"}

【当前任务】
{task.instruction}"""

                resp = await llm.ainvoke([SystemMessage(content=EXECUTE_SYSTEM_PROMPT), HumanMessage(content=prompt_text)])

                # Collect tool results
                if resp.tool_calls:
                    tool_outputs = []
                    for tc in resp.tool_calls:
                        tool = self.tools.get(tc["name"])
                        if tool:
                            try:
                                r = await tool.ainvoke(tc["args"])
                                tool_outputs.append(str(r))
                            except Exception as e:
                                tool_outputs.append(f"错误: {e}")
                    return TaskResult(task_id=task.id, success=True, output="\n".join(tool_outputs))
                else:
                    return TaskResult(task_id=task.id, success=True, output=str(resp.content))
            except Exception as e:
                last_error = e
                logger.warning("Task %s failed attempt %d/%d: %s", task.id, attempt + 1, self.max_tool_retries, e)

        return TaskResult(task_id=task.id, success=False, error=str(last_error) if last_error else "unknown error")

    async def _critique(self, state: OverallState) -> CritiqueResult:
        llm = self.llm.bind(response_format={"type": "json_object"})
        resp = await llm.ainvoke([
            SystemMessage(content=CRITIQUE_SYSTEM_PROMPT),
            HumanMessage(content=self._render_messages(state.messages)),
        ])
        try:
            data = json.loads(str(resp.content))
            return CritiqueResult(passed=data.get("passed", True), feedback=data.get("feedback", ""))
        except json.JSONDecodeError:
            return CritiqueResult(passed=True, feedback="")

    async def _summarize(self, state: OverallState) -> str:
        resp = await self.llm.ainvoke([
            SystemMessage(content=SUMMARIZE_SYSTEM_PROMPT),
            HumanMessage(content=f"""【用户原始问题】
{state.question}

【执行上下文】
{self._render_messages(state.messages)}"""),
        ])
        return str(resp.content)

    async def call(self, question: str) -> str:
        state = OverallState(question=question)
        state.messages.append(HumanMessage(content=question))

        while self.max_rounds <= 0 or state.round < self.max_rounds:
            state.round += 1
            logger.info("===== Plan-Execute Round %d =====", state.round)

            # 1. Plan
            plan = await self._generate_plan(state)
            state.plan = plan
            state.messages.append(AIMessage(content=f"【执行计划】\n{json.dumps([p.__dict__ for p in plan], ensure_ascii=False)}"))

            if not plan or all(p.id is None for p in plan):
                logger.info("No execution needed, direct answer")
                break

            # 2. Execute
            results = await self._execute_plan(state)
            state.task_results = results
            for tid, tr in results.items():
                state.messages.append(AIMessage(content=f"【任务结果 {tid}】 success={tr.success}\n{tr.output or tr.error}"))

            # 3. Critique
            critique = await self._critique(state)
            if critique.passed:
                logger.info("Goal satisfied, finish")
                break
            logger.info("Goal not satisfied: %s", critique.feedback)
            state.messages.append(AIMessage(content=f"【批判反馈】\n{critique.feedback}"))

            # 4. Compress if needed
            self._compress_if_needed(state)

        # 5. Summarize
        return await self._summarize(state)

    async def stream(self, question: str) -> AsyncIterator[str]:
        result = await self.call(question)
        yield result

    def _compress_if_needed(self, state: OverallState):
        if state.current_chars() <= self.context_char_limit:
            return
        logger.warning("Context too large, compressing...")
        # Keep first and last 4 messages, compress middle
        if len(state.messages) <= 6:
            return
        head = state.messages[:2]
        tail = state.messages[-4:]
        middle = state.messages[2:-4]
        compressed = "\n".join(f"[{m.__class__.__name__}]: {str(m.content)[:150]}..." for m in middle)
        state.messages = head + [SystemMessage(content=f"【压缩上下文】\n{compressed}")] + tail
