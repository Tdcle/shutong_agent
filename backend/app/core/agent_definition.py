"""AgentDefinition — metadata and runtime configuration for a registered agent."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentDefinition:
    name: str
    display_name: str
    description: str
    agent_cls: type
    system_prompt: str = ""
    tool_filter: list[str] | None = None
    keywords: list[str] = field(default_factory=list)
    priority: int = 0
    icon: str = "bot"
    requires_permission_broker: bool = False
