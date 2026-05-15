"""Dependency injection for API layer."""

from __future__ import annotations

from functools import lru_cache

from app.services.agent_service import AgentService
from app.tools.permissions import PermissionBroker

# Active permission brokers keyed by request_id (cleaned up after resolution)
_active_brokers: dict[str, PermissionBroker] = {}


def register_broker(broker: PermissionBroker, request_id: str):
    _active_brokers[request_id] = broker


def get_broker(request_id: str) -> PermissionBroker | None:
    return _active_brokers.get(request_id)


def unregister_broker(request_id: str):
    _active_brokers.pop(request_id, None)


@lru_cache()
def get_agent_service() -> AgentService:
    return AgentService()
