"""Short-term memory — L0 working memory buffer with structured, incremental summarization."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.config import settings

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """总结以下对话片段，提取对后续对话最有用的信息。输出中文，按以下格式：

## 用户信息
- 姓名、职业、偏好等

## 进行中的任务
- 当前在做什么，未完成的事项

## 关键决策
- 用户明确做出的选择或确定的方向

## 重要上下文
- 其他后续可能需要的信息

每项只写已知事实，不推测。无信息的项目省略。"""


@dataclass
class ShortTermMemory:
    session_id: str
    max_messages: int = 30
    char_limit: int = settings.context_char_limit
    _messages: list[BaseMessage] = field(default_factory=list, init=False)
    _summary_count: int = 0

    def add(self, message: BaseMessage):
        self._messages.append(message)
        if len(self._messages) > self.max_messages:
            self._trim_oldest()

    def add_many(self, messages: list[BaseMessage]):
        for m in messages:
            self.add(m)

    def load(self, messages: list[BaseMessage]):
        self._messages = list(messages)

    def get_messages(self) -> list[BaseMessage]:
        return list(self._messages)

    def get_last_n(self, n: int) -> list[BaseMessage]:
        return self._messages[-n:]

    def clear(self):
        self._messages.clear()
        self._summary_count = 0

    @property
    def total_chars(self) -> int:
        return sum(len(str(m.content or "")) for m in self._messages)

    def should_summarize(self) -> bool:
        return self.total_chars > self.char_limit

    def to_compressible(self) -> list[BaseMessage]:
        """Messages to compress: keep system prompts + latest 4 messages."""
        if len(self._messages) <= 8:
            return []
        return self._messages[2:-4]

    async def summarize_with(self, llm) -> str | None:
        """Summarize compressible messages, keeping incremental history."""
        to_compress = self.to_compressible()
        if not to_compress:
            return None

        # Find existing summaries to carry forward
        existing_summaries = [
            m for m in self._messages if isinstance(m, SystemMessage)
            and m.content and m.content.startswith("【对话摘要")
        ]

        text = "\n".join(
            f"[{m.__class__.__name__}]: {str(m.content)[:500]}"
            for m in to_compress if m.content
        )
        resp = await llm.ainvoke([
            SystemMessage(content=SUMMARY_PROMPT),
            HumanMessage(content=text),
        ])
        self._summary_count += 1

        # Build incremental summary: carry forward old + add new
        label = f"【对话摘要 #{self._summary_count}】"
        new_summary = SystemMessage(content=f"{label}\n{resp.content}")

        # Keep: system prompts + existing old summaries + new summary + latest 4 messages
        head = self._messages[:2]
        tail = self._messages[-4:]
        # Only keep last 3 summaries to bound growth
        keep_summaries = existing_summaries[-2:] if len(existing_summaries) > 2 else existing_summaries
        self._messages = head + keep_summaries + [new_summary] + tail

        logger.info("Summarized %d messages → summary #%d (%d chars)",
                     len(to_compress), self._summary_count, len(str(resp.content)))
        return str(resp.content)

    def _trim_oldest(self):
        for i, m in enumerate(self._messages):
            if not isinstance(m, SystemMessage):
                self._messages.pop(i)
                return
        self._messages.pop(0)
