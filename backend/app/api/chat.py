"""Chat API endpoints — SSE streaming equivalent to Java ReactAgentController.

Supports:
- SSE streaming with text + permission_request events
- Permission response endpoint for approving/denying tool calls
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_agent_service, register_broker, unregister_broker, get_broker
from app.models.database import async_session_factory
from app.models.session import Message, Session
from app.tools.permissions import PermissionBroker, set_current_permission_broker
from app.tools.workspace import create_session_workspace, set_session_workspace

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    session_id: str = Field(default="", description="会话ID，为空则创建新会话")
    message: str = Field(..., description="用户消息")
    agent_type: str = Field(default="react", description="Agent类型，固定使用react")


class ChatResponse(BaseModel):
    session_id: str
    message: str


class PermissionResponse(BaseModel):
    request_id: str = Field(..., description="权限请求ID")
    approved: bool = Field(..., description="是否批准")
    remember: bool = Field(default=False, description="本次会话内自动允许同类操作")


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """SSE streaming chat endpoint with permission support."""
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

    # Create or repair session workspace directory
    workspace_path = create_session_workspace(session_id)
    # Place a .session_id marker so the workspace is traceable back to the session
    marker = workspace_path / ".session_id"
    if not marker.exists():
        marker.write_text(session_id)

    # Create a permission broker for this stream
    permission_broker = PermissionBroker()

    async def event_generator():
        set_session_workspace(workspace_path)
        set_current_permission_broker(permission_broker)
        collected_content = ""
        try:
            async for event in agent_service.stream_chat(
                question=req.message,
                session_id=session_id,
                agent_type=req.agent_type,
                history=history,
                permission_broker=permission_broker,
            ):
                if isinstance(event, dict):
                    # Control event (e.g. permission_request)
                    request_id = event.get("request_id", "")
                    if request_id:
                        register_broker(permission_broker, request_id)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                else:
                    # Text token
                    collected_content += event
                    yield f"data: {json.dumps({'type': 'text', 'content': event}, ensure_ascii=False)}\n\n"

            # Save assistant message to DB
            async with async_session_factory() as db:
                db.add(Message(session_id=session_id, role="assistant", content=collected_content))
                await db.commit()

            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id})}\n\n"

            await agent_service.finalize_turn(session_id, req.message, collected_content)
        except Exception as e:
            logger.exception("Chat stream error")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        finally:
            set_current_permission_broker(None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/permission-response")
async def permission_response(resp: PermissionResponse):
    """User responds to a tool permission request."""
    broker = get_broker(resp.request_id)
    if broker is None:
        raise HTTPException(status_code=404, detail=f"Permission request not found: {resp.request_id}")
    broker.respond(resp.request_id, resp.approved, remember=resp.remember)
    unregister_broker(resp.request_id)
    return {"status": "ok", "request_id": resp.request_id, "approved": resp.approved, "remember": resp.remember}


@router.post("/send")
async def chat_send(req: ChatRequest) -> ChatResponse:
    """Non-streaming chat endpoint. Dangerous tools are auto-denied."""
    agent_service = get_agent_service()

    if not req.session_id:
        session = Session(title=req.message[:50])
        async with async_session_factory() as db:
            db.add(session)
            await db.commit()
            session_id = session.id
    else:
        session_id = req.session_id

    # Create or repair session workspace
    workspace_path = create_session_workspace(session_id)
    marker = workspace_path / ".session_id"
    if not marker.exists():
        marker.write_text(session_id)
    set_session_workspace(workspace_path)
    set_current_permission_broker(None)

    try:
        async with async_session_factory() as db:
            db.add(Message(session_id=session_id, role="user", content=req.message))
            await db.commit()

        async with async_session_factory() as db:
            result = await db.execute(
                select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
            )
            history = result.scalars().all()

        answer = await agent_service.send_chat(
            question=req.message,
            session_id=session_id,
            agent_type=req.agent_type,
            history=history,
        )

        async with async_session_factory() as db:
            db.add(Message(session_id=session_id, role="assistant", content=answer))
            await db.commit()

        await agent_service.finalize_turn(session_id, req.message, answer)

        return ChatResponse(session_id=session_id, message=answer)
    finally:
        pass
