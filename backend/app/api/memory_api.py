"""Memory management API — browse, search, create, and delete memories."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import get_agent_service

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryCreateRequest(BaseModel):
    name: str
    description: str = ""
    content: str
    type: str = "feedback"  # user, feedback, project, reference
    importance: float = 0.5


class MemorySearchRequest(BaseModel):
    query: str


@router.get("")
async def list_memories(type: str | None = None):
    service = get_agent_service()
    memories = service.memory_manager.list_memories(type)
    return {"memories": memories}


@router.get("/profile")
async def get_profile():
    service = get_agent_service()
    return service.memory_manager.get_profile()


@router.post("/search")
async def search_memory(req: MemorySearchRequest):
    service = get_agent_service()
    results = service.memory_manager.search_memories(req.query)
    return {"results": results}


@router.get("/{name}")
async def get_memory(name: str):
    service = get_agent_service()
    content = service.memory_manager.get_memory_content(name)
    if content is None:
        return {"error": f"Memory not found: {name}"}
    return {"name": name, "content": content}


@router.post("")
async def create_memory(req: MemoryCreateRequest):
    service = get_agent_service()
    path = service.memory_manager.save_memory(
        name=req.name,
        description=req.description,
        content=req.content,
        memory_type=req.type,
        importance=req.importance,
    )
    return {"ok": True, "path": path}


@router.delete("/{name}")
async def delete_memory(name: str):
    service = get_agent_service()
    service.memory_manager.delete_memory(name)
    return {"ok": True}
