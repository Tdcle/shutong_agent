"""Agent Service — orchestrates agent execution, memory, and tools for API layer."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import socket
from collections.abc import AsyncIterator
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from app.config import settings
from app.core import ReactAgent, PlanExecuteAgent, ReflectionAgent, HITLAgent
from app.memory.manager import MemoryManager
from app.memory.short_term import ShortTermMemory
from app.skill.registry import SkillRegistry
from app.tools.base import ToolRegistry, ToolDef
from app.tools.permissions import PermissionBroker
from app.tools.workspace import get_session_workspace

logger = logging.getLogger(__name__)


def _fn_to_langchain(td: ToolDef) -> StructuredTool:
    """Convert a ToolDef (sync or async function) to a LangChain StructuredTool."""
    import inspect
    if inspect.iscoroutinefunction(td.fn):
        return StructuredTool.from_function(
            coroutine=td.fn,
            name=td.name,
            description=td.description,
        )
    else:
        return StructuredTool.from_function(
            func=td.fn,
            name=td.name,
            description=td.description,
        )


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
        self._tool_defs: dict[str, ToolDef] = {}
        self._langchain_tools: list[StructuredTool] = []
        self._built = False

    def _ensure_built(self):
        if self._built:
            return
        self._build_tools()
        self._built = True

    def _build_tools(self):
        """Build tool registry with built-in tools + skill tools."""
        from app.tools.file_ops import (
            read_file, write_file, edit_file, grep, glob, move_file, delete_file, list_files,
        )
        from app.tools.search import search_web
        from app.tools.shell import execute_shell

        self.tool_registry = ToolRegistry()

        # Helper to register a decorated tool function
        def register(fn):
            name = getattr(fn, "_tool_name", fn.__name__)
            desc = getattr(fn, "_tool_description", "")
            params = getattr(fn, "_tool_params", {})
            perm = getattr(fn, "_tool_permission_level", "read")
            self.tool_registry.register(name, desc, fn, params, permission_level=perm)

        # File tools
        for fn in [read_file, write_file, edit_file, grep, glob, move_file, delete_file, list_files]:
            register(fn)

        # Search
        register(search_web)

        # Shell
        register(execute_shell)

        # Skill read tool
        skill_tool = self.skill_registry.create_read_skill_tool()
        self.tool_registry.register(
            skill_tool.name, skill_tool.description, skill_tool.fn,
            skill_tool.parameters, permission_level="read",
        )

        # Build lookup map
        self._tool_defs = {td.name: td for td in self.tool_registry.get_all()}

        # Convert to LangChain tools
        self._langchain_tools = [_fn_to_langchain(td) for td in self.tool_registry.get_all()]

    def _db_messages_to_lc(self, db_messages: list) -> list[BaseMessage]:
        """Convert DB Message objects to LangChain messages."""
        lc_messages = []
        for m in db_messages[-30:]:
            if m.role == "user":
                lc_messages.append(HumanMessage(content=m.content))
            elif m.role == "assistant":
                lc_messages.append(AIMessage(content=m.content))
            elif m.role == "system":
                lc_messages.append(SystemMessage(content=m.content))
        return lc_messages

    def _build_system_prompt(self, workspace_path: str = "") -> str:
        """Build system prompt with skill list and runtime environment info."""
        parts = []

        # Runtime environment info — tells the agent where it's running
        home = str(Path.home())
        desktop = str(Path.home() / "Desktop")
        hostname = socket.gethostname()

        env_lines = [
            "## 运行环境",
            f"- 操作系统: {platform.system()} {platform.release()}",
            f"- 主机名: {hostname}",
            f"- 用户主目录: {home}",
            f"- 桌面路径: {desktop}",
        ]
        if workspace_path:
            env_lines.append(f"- 当前工作目录: {workspace_path}")
        env_lines.append("")
        env_lines.append('相对路径默认解析到工作目录。用户说"桌面"时请使用上面的桌面路径。')
        parts.append("\n".join(env_lines))

        skills_prompt = self.skill_registry.get_skills_prompt()
        if skills_prompt:
            parts.append(skills_prompt)
        return "\n\n".join(parts)

    def _make_react_agent(self, system_prompt: str) -> ReactAgent:
        return ReactAgent(
            tools=self._langchain_tools,
            llm=self.llm,
            system_prompt=system_prompt,
            tool_defs=self._tool_defs,
        )

    async def stream_chat(
        self,
        question: str,
        session_id: str,
        agent_type: str = "react",
        history: list | None = None,
        permission_broker: PermissionBroker | None = None,
    ) -> AsyncIterator[str | dict]:
        self._ensure_built()
        lc_history = self._db_messages_to_lc(history) if history else []

        # Load L1 memory context
        try:
            l1_context = self.memory_manager.load_l1_context(query=question)
            if l1_context:
                lc_history.insert(0, SystemMessage(content=l1_context))
        except Exception as e:
            logger.warning("Failed to load memory context: %s", e)

        ws = get_session_workspace()
        system_prompt = self._build_system_prompt(str(ws) if ws else "")

        if agent_type == "plan_execute":
            agent = PlanExecuteAgent(tools=self._langchain_tools, llm=self.llm)
            result = await agent.call(question)
            yield str(result)
        elif agent_type == "reflection":
            agent = ReflectionAgent(
                tools=self._langchain_tools,
                llm=self.llm,
                system_prompt=system_prompt,
            )
            async for chunk in agent.stream(question, lc_history):
                yield chunk
        else:  # react
            agent = self._make_react_agent(system_prompt)
            async for event in agent.stream(question, lc_history, permission_broker=permission_broker):
                yield event

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
        self._ensure_built()
        lc_history = self._db_messages_to_lc(history) if history else []
        ws = get_session_workspace()
        system_prompt = self._build_system_prompt(str(ws) if ws else "")

        if agent_type == "plan_execute":
            agent = PlanExecuteAgent(tools=self._langchain_tools, llm=self.llm)
        elif agent_type == "reflection":
            agent = ReflectionAgent(tools=self._langchain_tools, llm=self.llm, system_prompt=system_prompt)
        else:
            agent = self._make_react_agent(system_prompt)

        result = await agent.call(question, lc_history)
        return result
