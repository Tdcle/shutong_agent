"""Agent Service — orchestrates agent execution, memory, and tools for API layer."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from app.config import settings
from app.core import ReactAgent, PlanExecuteAgent, ReflectionAgent, HITLAgent
from app.memory.manager import MemoryManager
from app.memory.short_term import ShortTermMemory
from app.skill.registry import SkillRegistry
from app.tools.base import ToolRegistry

logger = logging.getLogger(__name__)


class AgentService:
    def __init__(self):
        self.llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
        self.memory_manager = MemoryManager(self.llm)
        self.skill_registry = SkillRegistry(settings.skills_dir)
        self.skill_registry.load_all()
        self._build_tools()

    def _build_tools(self):
        """Build tool registry with built-in tools + skill tools."""
        from app.tools.search import search_web
        from app.tools.file_ops import read_file, write_file, list_files
        from app.tools.shell import execute_shell

        self.tool_registry = ToolRegistry()

        # Built-in tools
        self.tool_registry.register("search_web", "搜索互联网获取最新信息", search_web)
        self.tool_registry.register("read_file", "读取文件内容", read_file)
        self.tool_registry.register("write_file", "写入内容到文件", write_file)
        self.tool_registry.register("list_files", "列出目录中的文件", list_files)
        self.tool_registry.register("execute_shell", "执行Shell命令", execute_shell)

        # Skill read tool
        skill_tool = self.skill_registry.create_read_skill_tool()
        self.tool_registry.register(
            skill_tool.name,
            skill_tool.description,
            skill_tool.fn,
            skill_tool.parameters,
        )

        # Convert to LangChain tools
        self._langchain_tools = []
        for td in self.tool_registry.get_all():
            # Wrap async/sync functions
            import inspect
            if inspect.iscoroutinefunction(td.fn):
                lc_tool = StructuredTool.from_function(
                    coroutine=td.fn,
                    name=td.name,
                    description=td.description,
                )
            else:
                lc_tool = StructuredTool.from_function(
                    func=td.fn,
                    name=td.name,
                    description=td.description,
                )
            self._langchain_tools.append(lc_tool)

    def _db_messages_to_lc(self, db_messages: list) -> list[BaseMessage]:
        """Convert DB Message objects to LangChain messages."""
        lc_messages = []
        for m in db_messages[-30:]:  # Last 30 messages
            if m.role == "user":
                lc_messages.append(HumanMessage(content=m.content))
            elif m.role == "assistant":
                lc_messages.append(AIMessage(content=m.content))
            elif m.role == "system":
                lc_messages.append(SystemMessage(content=m.content))
            # tool role messages are handled as part of AIMessage tool_calls
        return lc_messages

    def _build_system_prompt(self) -> str:
        """Build system prompt with skill list."""
        parts = []
        skills_prompt = self.skill_registry.get_skills_prompt()
        if skills_prompt:
            parts.append(skills_prompt)
        return "\n\n".join(parts)

    async def stream_chat(
        self,
        question: str,
        session_id: str,
        agent_type: str = "react",
        history: list | None = None,
    ) -> AsyncIterator[str]:
        lc_history = self._db_messages_to_lc(history) if history else []

        # Build L1 context (always-loaded memory)
        try:
            l1_context = self.memory_manager.load_l1_context(query=question)
            if l1_context:
                lc_history.insert(0, SystemMessage(content=l1_context))
        except Exception as e:
            logger.warning("Failed to load memory context: %s", e)

        system_prompt = self._build_system_prompt()

        if agent_type == "plan_execute":
            agent = PlanExecuteAgent(
                tools=self._langchain_tools,
                llm=self.llm,
            )
            async for chunk in agent.stream(question):
                yield chunk
        elif agent_type == "reflection":
            agent = ReflectionAgent(
                tools=self._langchain_tools,
                llm=self.llm,
                system_prompt=system_prompt,
            )
            async for chunk in agent.stream(question, lc_history):
                yield chunk
        else:  # react (default)
            agent = ReactAgent(
                tools=self._langchain_tools,
                llm=self.llm,
                system_prompt=system_prompt,
            )
            async for chunk in agent.stream(question, lc_history):
                yield chunk

    async def finalize_turn(self, session_id: str, user_msg: str, assistant_msg: str):
        """Called after each conversation turn to persist memories."""
        try:
            await self.memory_manager.post_turn(session_id, user_msg, assistant_msg)
        except Exception as e:
            logger.warning("Memory post_turn failed: %s", e)

    async def send_chat(
        self,
        question: str,
        session_id: str,
        agent_type: str = "react",
        history: list | None = None,
    ) -> str:
        """Non-streaming chat."""
        lc_history = self._db_messages_to_lc(history) if history else []
        system_prompt = self._build_system_prompt()

        if agent_type == "plan_execute":
            agent = PlanExecuteAgent(tools=self._langchain_tools, llm=self.llm)
        elif agent_type == "reflection":
            agent = ReflectionAgent(tools=self._langchain_tools, llm=self.llm, system_prompt=system_prompt)
        else:
            agent = ReactAgent(tools=self._langchain_tools, llm=self.llm, system_prompt=system_prompt)

        result = await agent.call(question, lc_history)
        return result
