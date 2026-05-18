"""Agents listing API."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import get_agent_service

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
async def list_agents():
    agent_service = get_agent_service()
    agent_service._ensure_agents_built()
    agents = agent_service.agent_registry.list_all()
    return {
        "agents": [
            {
                "name": a.name,
                "display_name": a.display_name,
                "description": a.description,
                "icon": a.icon,
                "keywords": a.keywords,
            }
            for a in agents
        ],
        "default": "react",
    }
