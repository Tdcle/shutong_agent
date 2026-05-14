"""Skills management API."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.skill.registry import SkillRegistry
from app.skill.loader import SkillLoader
from app.config import settings

router = APIRouter(prefix="/api/skills", tags=["skills"])

_registry = SkillRegistry(settings.skills_dir)
_registry.load_all()
_loader = SkillLoader(_registry)


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    content: str


@router.get("")
async def list_skills():
    skills = _registry.list_all()
    return {
        "skills": [
            {"name": s.name, "description": s.description}
            for s in skills
        ]
    }


@router.get("/{skill_name}")
async def get_skill(skill_name: str):
    skill = _registry.get(skill_name)
    if not skill:
        return {"error": f"Skill not found: {skill_name}"}
    return {"name": skill.name, "description": skill.description, "content": skill.content}


@router.post("")
async def create_skill(req: SkillCreateRequest):
    path = _loader.create_skill(req.name, req.description, req.content)
    return {"ok": True, "path": path}


@router.delete("/{skill_name}")
async def delete_skill(skill_name: str):
    _loader.delete_skill(skill_name)
    return {"ok": True}


@router.post("/reload")
async def reload_skills():
    _registry.reload()
    return {"ok": True, "count": len(_registry.list_all())}
