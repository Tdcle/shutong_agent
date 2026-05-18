from .agent import ReactAgent
from .plan_execute import PlanExecuteAgent
from .reflection import ReflectionAgent
from .hitl import HITLAgent, HITLState, AgentInterrupted, AgentFinished
from .agent_definition import AgentDefinition
from .agent_registry import AgentRegistry
from .agent_router import AgentRouter
from .deep_analysis import DeepAnalysisSubAgent
from .code_agent_sub import CodeSubAgent
from .search_agent import SearchSubAgent

__all__ = [
    "ReactAgent",
    "PlanExecuteAgent",
    "ReflectionAgent",
    "HITLAgent",
    "HITLState",
    "AgentInterrupted",
    "AgentFinished",
    "AgentDefinition",
    "AgentRegistry",
    "AgentRouter",
    "DeepAnalysisSubAgent",
    "CodeSubAgent",
    "SearchSubAgent",
]


def create_default_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.load_builtins()
    registry.load_from_files()
    return registry
