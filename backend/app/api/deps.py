"""Dependency injection for API layer."""

from __future__ import annotations

from functools import lru_cache

from app.services.agent_service import AgentService


@lru_cache()
def get_agent_service() -> AgentService:
    return AgentService()
