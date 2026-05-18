"""Agent service，负责组装工具、系统提示词和会话执行入口。"""

from __future__ import annotations

import logging
import platform
import socket
from collections.abc import AsyncIterator
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from app.config import settings
from app.core import PlanExecuteAgent, ReactAgent, ReflectionAgent
from app.memory.manager import MemoryManager
from app.skill.registry import SkillRegistry
from app.tools.base import ToolDef, ToolRegistry
from app.tools.permissions import PermissionBroker
from app.tools.workspace import get_session_workspace

logger = logging.getLogger(__name__)


def _fn_to_langchain(tool_def: ToolDef) -> StructuredTool:
    """Convert ToolDef to a LangChain StructuredTool."""
    import inspect

    if inspect.iscoroutinefunction(tool_def.fn):
        return StructuredTool.from_function(
            coroutine=tool_def.fn,
            name=tool_def.name,
            description=tool_def.description,
        )
    return StructuredTool.from_function(
        func=tool_def.fn,
        name=tool_def.name,
        description=tool_def.description,
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
        """Build tool registry with built-in tools and skill tools."""
        from app.tools.file_ops import (
            copy_file,
            copy_paths,
            delete_file,
            delete_paths,
            edit_file,
            glob,
            grep,
            list_files,
            move_file,
            move_paths,
            read_file,
            write_file,
        )
        from app.tools.image_ops import analyze_image as analyze_image_fn
        from app.tools.search import search_web
        from app.tools.shell import execute_python, execute_bash
        from app.tools.document_ops import read_document
        from app.tools.deep_analysis_tool import start_deep_analysis
        from app.tools.code_agent_tool import start_code_agent
        from app.tools.search_agent_tool import start_search_agent

        self.tool_registry = ToolRegistry()

        def register(fn):
            name = getattr(fn, "_tool_name", fn.__name__)
            desc = getattr(fn, "_tool_description", "")
            params = getattr(fn, "_tool_params", {})
            perm = getattr(fn, "_tool_permission_level", "read")
            visible = getattr(fn, "_tool_visible", True)
            self.tool_registry.register(name, desc, fn, params, permission_level=perm, visible=visible)

        for fn in [
            read_file,
            write_file,
            edit_file,
            grep,
            glob,
            move_file,
            copy_file,
            delete_file,
            list_files,
            move_paths,
            copy_paths,
            delete_paths,
        ]:
            register(fn)

        register(analyze_image_fn)
        register(search_web)
        register(execute_bash)
        register(execute_python)
        register(read_document)
        register(start_deep_analysis)
        register(start_code_agent)
        register(start_search_agent)

        skill_tool = self.skill_registry.create_read_skill_tool()
        self.tool_registry.register(
            skill_tool.name,
            skill_tool.description,
            skill_tool.fn,
            skill_tool.parameters,
            permission_level="read",
        )

        self._tool_defs = {tool_def.name: tool_def for tool_def in self.tool_registry.get_all()}
        self._langchain_tools = [_fn_to_langchain(tool_def) for tool_def in self.tool_registry.get_all()]

    def _db_messages_to_lc(self, db_messages: list) -> list[BaseMessage]:
        """Convert DB Message objects to LangChain messages."""
        lc_messages: list[BaseMessage] = []
        limit = settings.short_term_max_messages
        for message in db_messages[-limit:]:
            if message.role == "user":
                lc_messages.append(HumanMessage(content=message.content))
            elif message.role == "assistant":
                lc_messages.append(AIMessage(content=message.content))
            elif message.role == "system":
                lc_messages.append(SystemMessage(content=message.content))
        return lc_messages

    def _build_system_prompt(self, workspace_path: str = "") -> str:
        """Build the runtime prompt with environment details and installed skills."""
        parts: list[str] = []

        home = str(Path.home())
        desktop = str(Path.home() / "Desktop")
        hostname = socket.gethostname()

        env_lines = [
            '## Running Environment (IMPORTANT - these paths are real, follow them strictly)',
            '- OS: **' + platform.system() + ' ' + platform.release() + '** (NOT Linux - do NOT use /root, /home etc.)',
            '- Hostname: ' + hostname,
            '- Home: ' + home,
            '- Desktop: ' + desktop,
        ]
        if workspace_path:
            env_lines.append('- **Workspace (default working dir): ' + workspace_path + '**')
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

        try:
            l1_context = self.memory_manager.load_l1_context(query=question)
            if l1_context:
                lc_history.insert(0, SystemMessage(content=l1_context))
        except Exception as exc:
            logger.warning("Failed to load memory context: %s", exc)

        workspace = get_session_workspace()
        system_prompt = self._build_system_prompt(str(workspace) if workspace else "")

        if agent_type == "plan_execute":
            agent = PlanExecuteAgent(tools=self._langchain_tools, llm=self.llm)
            result = await agent.call(question)
            yield str(result)
            return

        if agent_type == "reflection":
            agent = ReflectionAgent(
                tools=self._langchain_tools,
                llm=self.llm,
                system_prompt=system_prompt,
            )
            async for chunk in agent.stream(question, lc_history):
                yield chunk
            return

        agent = self._make_react_agent(system_prompt)
        async for event in agent.stream(question, lc_history, permission_broker=permission_broker):
            yield event

    async def finalize_turn(self, session_id: str, user_msg: str, assistant_msg: str):
        """Called after each conversation turn to persist memories."""
        try:
            await self.memory_manager.post_turn(session_id, user_msg, assistant_msg)
        except Exception as exc:
            logger.warning("Memory post_turn failed: %s", exc)

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
        workspace = get_session_workspace()
        system_prompt = self._build_system_prompt(str(workspace) if workspace else "")

        if agent_type == "plan_execute":
            agent = PlanExecuteAgent(tools=self._langchain_tools, llm=self.llm)
        elif agent_type == "reflection":
            agent = ReflectionAgent(
                tools=self._langchain_tools,
                llm=self.llm,
                system_prompt=system_prompt,
            )
        else:
            agent = self._make_react_agent(system_prompt)

        return await agent.call(question, lc_history)
