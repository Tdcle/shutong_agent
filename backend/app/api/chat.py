"""Chat API endpoints — SSE streaming equivalent to Java ReactAgentController."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_agent_service
from app.models.database import async_session_factory
from app.models.session import Message, Session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    session_id: str = Field(default="", description="会话ID，为空则创建新会话")
    message: str = Field(..., description="用户消息")
    agent_type: str = Field(default="react", description="Agent类型: react, plan_execute, reflection")


class ChatResponse(BaseModel):
    session_id: str
    message: str


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """SSE streaming chat endpoint — equivalent to Java's Sinks.Many + Flux streaming."""
    agent_service = get_agent_service()

    # Create or load session
    if not req.session_id:
        session = Session(title=req.message[:50])
        async with async_session_factory() as db:
            db.add(session)
            await db.commit()
            session_id = session.id
    else:
        session_id = req.session_id

    # Save user message to DB
    async with async_session_factory() as db:
        db.add(Message(session_id=session_id, role="user", content=req.message))
        await db.commit()

    # Load history
    async with async_session_factory() as db:
        result = await db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
        )
        db_messages = result.scalars().all()
        history = [m for m in db_messages]

    async def event_generator():
        collected_content = ""
        try:
            async for chunk in agent_service.stream_chat(
                question=req.message,
                session_id=session_id,
                agent_type=req.agent_type,
                history=history,
            ):
                collected_content += chunk
                yield f"data: {json.dumps({'type': 'text', 'content': chunk}, ensure_ascii=False)}\n\n"

            # Save assistant message to DB
            async with async_session_factory() as db:
                db.add(Message(session_id=session_id, role="assistant", content=collected_content))
                await db.commit()

            # Send done first to unlock the UI
            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id})}\n\n"

            # Memory extraction runs after user already has the response
            await agent_service.finalize_turn(session_id, req.message, collected_content)
        except Exception as e:
            logger.exception("Chat stream error")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/send")
async def chat_send(req: ChatRequest) -> ChatResponse:
    """Non-streaming chat endpoint."""
    agent_service = get_agent_service()

    if not req.session_id:
        session = Session(title=req.message[:50])
        async with async_session_factory() as db:
            db.add(session)
            await db.commit()
            session_id = session.id
    else:
        session_id = req.session_id

    # Save user message
    async with async_session_factory() as db:
        db.add(Message(session_id=session_id, role="user", content=req.message))
        await db.commit()

    # Load history
    async with async_session_factory() as db:
        result = await db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
        )
        history = result.scalars().all()

    # Get response
    answer = await agent_service.send_chat(
        question=req.message,
        session_id=session_id,
        agent_type=req.agent_type,
        history=history,
    )

    # Save assistant message
    async with async_session_factory() as db:
        db.add(Message(session_id=session_id, role="assistant", content=answer))
        await db.commit()

    # Trigger memory extraction
    await agent_service.finalize_turn(session_id, req.message, answer)

    return ChatResponse(session_id=session_id, message=answer)
