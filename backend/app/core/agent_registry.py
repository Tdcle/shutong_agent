"""AgentRegistry — loads agents from code registration and AGENT.md files."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.core.agent_definition import AgentDefinition

logger = logging.getLogger(__name__)

_AGENT_CLASS_MAP: dict[str, type] = {}


def _lazy_import_agent_classes():
    if _AGENT_CLASS_MAP:
        return
    from app.core.agent import ReactAgent
    from app.core.plan_execute import PlanExecuteAgent
    from app.core.reflection import ReflectionAgent

    _AGENT_CLASS_MAP.update(
        {
            "react": ReactAgent,
            "plan_execute": PlanExecuteAgent,
            "reflection": ReflectionAgent,
        }
    )


class AgentRegistry:
    def __init__(self, agents_dir: str | None = None):
        self._agents: dict[str, AgentDefinition] = {}
        self._agents_dir = Path(agents_dir) if agents_dir else Path(__file__).parent.parent.parent / "agents"
        self._loaded = False

    def register(self, definition: AgentDefinition):
        if definition.name in self._agents:
            logger.warning("Agent '%s' already registered, overwriting", definition.name)
        self._agents[definition.name] = definition
        logger.info("Registered agent: %s (%s)", definition.name, definition.display_name)

    def get(self, name: str) -> AgentDefinition | None:
        return self._agents.get(name)

    def list_all(self) -> list[AgentDefinition]:
        return list(self._agents.values())

    def load_builtins(self):
        _lazy_import_agent_classes()

        self.register(
            AgentDefinition(
                name="react",
                display_name="ReAct推理",
                description="通用对话、代码编写、文件操作、日常任务处理",
                agent_cls=_AGENT_CLASS_MAP["react"],
                keywords=[],
                priority=0,
                icon="bot",
                requires_permission_broker=True,
            )
        )

        self.register(
            AgentDefinition(
                name="plan_execute",
                display_name="Plan-Execute规划",
                description="复杂多步骤任务，需要先制定计划再逐步执行，适合搭建项目、批量生成等",
                agent_cls=_AGENT_CLASS_MAP["plan_execute"],
                keywords=[
                    "创建项目", "搭建", "构建", "多步骤", "实施计划", "从零开始",
                    "批量生成", "生成项目", "脚手架",
                    "build", "create project", "setup", "scaffold", "generate project",
                ],
                priority=1,
                icon="plan",
            )
        )

        self.register(
            AgentDefinition(
                name="reflection",
                display_name="反思审查",
                description="需要深度分析、代码审查、多次反思和改进后给出高质量答案的任务",
                agent_cls=_AGENT_CLASS_MAP["reflection"],
                keywords=[
                    "审查", "review", "改进", "分析代码", "code review", "复盘",
                    "检查", "评估质量", "优化建议", "refactor", "深度分析",
                ],
                priority=2,
                icon="reflect",
            )
        )

    def load_from_files(self):
        if not self._agents_dir.exists():
            return

        _lazy_import_agent_classes()

        for agent_md in self._agents_dir.rglob("AGENT.md"):
            try:
                definition = self._parse_agent_md(agent_md)
                if definition:
                    self.register(definition)
            except Exception as e:
                logger.error("Failed to load agent definition %s: %s", agent_md, e)

    def _parse_agent_md(self, path: Path) -> AgentDefinition | None:
        raw = path.read_text(encoding="utf-8")
        lines = raw.split("\n")
        if not lines or lines[0].strip() != "---":
            return None

        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break
        if end_idx is None:
            return None

        metadata: dict[str, Any] = {}
        for line in lines[1:end_idx]:
            line = line.strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
            metadata[key] = value

        name = metadata.get("name", path.parent.name)
        agent_class_name = metadata.get("agent_class", "react")
        agent_cls = _AGENT_CLASS_MAP.get(agent_class_name)
        if agent_cls is None:
            logger.warning("Unknown agent_class '%s' in %s", agent_class_name, path)
            return None

        body = "\n".join(lines[end_idx + 1:]).strip()

        return AgentDefinition(
            name=name,
            display_name=metadata.get("display_name", name),
            description=metadata.get("description", body[:100] if body else ""),
            agent_cls=agent_cls,
            system_prompt=body,
            tool_filter=metadata.get("tool_filter"),
            keywords=metadata.get("keywords", []),
            priority=metadata.get("priority", 5),
            icon=metadata.get("icon", "bot"),
            requires_permission_broker=metadata.get("requires_permission_broker", False),
        )
