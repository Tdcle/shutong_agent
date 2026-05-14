"""Session management API — migrated from Java SessionController."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import async_session_factory
from app.models.session import Message, Session

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionListItem(BaseModel):
    id: str
    title: str
    status: str
    message_count: int
    created_at: str
    updated_at: str


class SessionDetail(BaseModel):
    id: str
    title: str
    status: str
    messages: list[dict]
    created_at: str
    updated_at: str


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    status: str | None = None


class DeleteRequest(BaseModel):
    cascade: bool = False


@router.get("")
async def list_sessions() -> list[SessionListItem]:
    async with async_session_factory() as db:
        result = await db.execute(
            select(Session).order_by(Session.updated_at.desc()).limit(50)
        )
        sessions = result.scalars().all()

        items = []
        for s in sessions:
            count_result = await db.execute(
                select(Message).where(Message.session_id == s.id)
            )
            msgs = count_result.scalars().all()
            items.append(SessionListItem(
                id=s.id,
                title=s.title or "新对话",
                status=s.status or "active",
                message_count=len(msgs),
                created_at=s.created_at.isoformat() if s.created_at else "",
                updated_at=s.updated_at.isoformat() if s.updated_at else "",
            ))
        return items


@router.get("/{session_id}")
async def get_session(session_id: str) -> SessionDetail:
    async with async_session_factory() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        msg_result = await db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
        )
        messages = msg_result.scalars().all()

        return SessionDetail(
            id=session.id,
            title=session.title or "新对话",
            status=session.status or "active",
            messages=[
                {"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat() if m.created_at else ""}
                for m in messages
            ],
            created_at=session.created_at.isoformat() if session.created_at else "",
            updated_at=session.updated_at.isoformat() if session.updated_at else "",
        )


@router.put("/{session_id}")
async def update_session(session_id: str, req: UpdateSessionRequest) -> dict:
    async with async_session_factory() as db:
        updates = {}
        if req.title is not None:
            updates["title"] = req.title
        if req.status is not None:
            updates["status"] = req.status
        if updates:
            updates["updated_at"] = datetime.now(timezone.utc)
            await db.execute(
                update(Session).where(Session.id == session_id).values(**updates)
            )
            await db.commit()
        return {"ok": True}


@router.delete("/{session_id}")
async def delete_session(session_id: str) -> dict:
    async with async_session_factory() as db:
        await db.execute(delete(Session).where(Session.id == session_id))
        await db.commit()
        return {"ok": True}
