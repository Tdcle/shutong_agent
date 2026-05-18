"""Chat API endpoints — SSE streaming equivalent to Java ReactAgentController.

Supports:
- SSE streaming with text + permission_request events
- Permission response endpoint for approving/denying tool calls
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_agent_service, register_broker, unregister_broker, get_broker
from app.config import settings
from app.models.database import async_session_factory
from app.models.session import Message, Session
from app.tools.permissions import PermissionBroker, set_current_permission_broker
from app.tools.workspace import create_session_workspace, set_session_workspace

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


async def _get_or_create_session(session_id: str, message: str) -> str:
    """Return existing session_id or create a new session and return its id.

    Also updates the title if it's still the default, so the sidebar shows a
    meaningful name instead of always showing the placeholder.
    """
    DEFAULT_TITLES = {"新对话", "新对话"}
    if session_id:
        # Update title if still default
        async with async_session_factory() as db:
            result = await db.execute(select(Session).where(Session.id == session_id))
            session = result.scalar_one_or_none()
            if session and session.title in DEFAULT_TITLES:
                session.title = message[:50]
                await db.commit()
        return session_id
    session = Session(title=message[:50])
    async with async_session_factory() as db:
        db.add(session)
        await db.commit()
        return session.id


def _ensure_workspace(session_id: str) -> Path:
    """Create or repair session workspace and set the .session_id marker."""
    workspace_path = create_session_workspace(session_id)
    marker = workspace_path / ".session_id"
    if not marker.exists():
        marker.write_text(session_id)
    return workspace_path


def _enrich_attachments(raw_paths: list[str]) -> str | None:
    """Convert a list of file paths into a JSON array of structured attachment objects."""
    if not raw_paths:
        return None
    attachments = []
    for p in raw_paths:
        ext = Path(p).suffix.lower()
        attachments.append({
            "path": p,
            "filename": Path(p).name,
            "type": _file_category(ext),
        })
    return json.dumps(attachments, ensure_ascii=False)


def _build_augmented_question(
    message: str,
    attachment_paths: list[str],
    *,
    deep_analysis: bool = False,
) -> str:
    """Build the question with auto-injected document content and image references.

    Normal mode: documents auto-read and injected, images listed as paths.
    Deep analysis mode: documents stashed for sub-agent, main agent gets brief summary.
    """
    if not attachment_paths:
        return message

    img_paths = []
    doc_paths = []
    for p in attachment_paths:
        ext = Path(p).suffix.lower()
        if ext in _IMAGE_EXTS:
            img_paths.append(p)
        else:
            doc_paths.append(p)

    parts = [message]

    if deep_analysis:
        # Stash document contents for the sub-agent, give main agent only a summary
        if doc_paths:
            from app.tools.document_ops import read_document as read_doc_fn
            from app.tools.deep_analysis_tool import stash_documents
            stashed = {}
            doc_hints = []
            for p in doc_paths:
                filename = Path(p).name
                try:
                    content = read_doc_fn(p)
                    stashed[filename] = content
                    preview = content[:300]
                    doc_hints.append(
                        f"- {filename}（{len(content)} 字符，开头：{preview}...）"
                    )
                except Exception:
                    doc_hints.append(f"- {filename}（读取失败，可重试）")
            stash_documents(stashed)
            parts.insert(0, (
                "[深度分析模式]\n"
                "用户上传了以下文档，如需深入分析请调用 start_deep_analysis 工具，"
                "将 document_names 指定为对应的文件名。\n\n"
                + "\n".join(doc_hints)
            ))
    else:
        # Normal mode: auto-inject document content
        if doc_paths:
            from app.tools.document_ops import read_document as read_doc_fn
            doc_texts = []
            for p in doc_paths:
                filename = Path(p).name
                try:
                    content = read_doc_fn(p)
                    doc_texts.append(f"[用户上传的文件：{filename}]\n\n{content}")
                except Exception:
                    doc_texts.append(f"[用户上传的文件：{filename}]\n（读取失败，模型可手动调用 read_document 工具重试）")
            parts.insert(0, "\n\n---\n\n".join(doc_texts))

    # Image references
    if img_paths:
        img_lines = "\n".join(f"- {img}" for img in img_paths)
        parts.append(f"\n[用户上传的图片]\n{img_lines}")

    return "\n\n".join(parts)


class ChatRequest(BaseModel):
    session_id: str = Field(default="", description="会话ID，为空则创建新会话")
    message: str = Field(..., description="用户消息")
    agent_type: str = Field(default="auto", description="Agent类型，auto表示自动选择")
    images: list[str] = Field(default=[], description="已上传的图片路径列表")
    deep_analysis: bool = Field(default=False, description="是否启用深度分析模式")


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

    session_id = await _get_or_create_session(req.session_id, req.message)

    # Load history BEFORE saving current message (avoid duplication)
    async with async_session_factory() as db:
        result = await db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
        )
        db_messages = result.scalars().all()
        history = [m for m in db_messages]

    # Save user message to DB
    async with async_session_factory() as db:
        db.add(Message(
            session_id=session_id,
            role="user",
            content=req.message,
            images=_enrich_attachments(req.images),
        ))
        await db.commit()

    # Create or repair session workspace
    workspace_path = _ensure_workspace(session_id)

    # Create a permission broker for this stream
    permission_broker = PermissionBroker()

    async def event_generator():
        set_session_workspace(workspace_path)
        set_current_permission_broker(permission_broker)
        collected_content = ""
        resolved_agent = "react"
        tool_events: list[dict] = []

        # Build question with auto-injected document content
        question = await asyncio.to_thread(
            _build_augmented_question, req.message, req.images,
            deep_analysis=req.deep_analysis,
        )

        try:
            async for event in agent_service.stream_chat(
                question=question,
                session_id=session_id,
                agent_type=req.agent_type,
                history=history,
                permission_broker=permission_broker,
            ):
                if isinstance(event, dict):
                    if event.get("type") == "agent_info":
                        resolved_agent = event.get("agent_type", "react")
                    elif event.get("type") == "tool_call":
                        tool_events.append({
                            "tool": event.get("tool", ""),
                            "args": event.get("args", {}),
                            "status": "running",
                            "visible": event.get("visible", True),
                        })
                    elif event.get("type") == "tool_result":
                        # Find the matching running tool_call and update it
                        for te in reversed(tool_events):
                            if te.get("tool") == event.get("tool") and te.get("status") == "running":
                                te["status"] = "done"
                                te["success"] = event.get("success")
                                te["warning"] = event.get("warning", False)
                                te["result"] = event.get("result", "")
                                te["visible"] = event.get("visible", True)
                                break
                    # Control event (e.g. permission_request)
                    request_id = event.get("request_id", "")
                    if request_id:
                        register_broker(permission_broker, request_id)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                else:
                    # Text token
                    collected_content += event
                    yield f"data: {json.dumps({'type': 'text', 'content': event}, ensure_ascii=False)}\n\n"

            # Save assistant message to DB with tool call info
            async with async_session_factory() as db:
                db.add(Message(
                    session_id=session_id,
                    role="assistant",
                    content=collected_content,
                    tool_calls=json.dumps(tool_events, ensure_ascii=False) if tool_events else None,
                ))
                await db.commit()

            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id, 'agent_type': resolved_agent})}\n\n"

            await agent_service.finalize_turn(session_id, req.message, collected_content)
        except Exception as e:
            logger.exception("Chat stream error")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        finally:
            set_current_permission_broker(None)
            from app.tools.deep_analysis_tool import stash_documents
            stash_documents(None)

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

    session_id = await _get_or_create_session(req.session_id, req.message)
    workspace_path = _ensure_workspace(session_id)
    set_session_workspace(workspace_path)
    set_current_permission_broker(None)

    try:
        # Load history BEFORE saving current message (avoid duplication)
        async with async_session_factory() as db:
            result = await db.execute(
                select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
            )
            history = result.scalars().all()

        async with async_session_factory() as db:
            db.add(Message(
                session_id=session_id,
                role="user",
                content=req.message,
                images=_enrich_attachments(req.images),
            ))
            await db.commit()

        question = await asyncio.to_thread(
            _build_augmented_question, req.message, req.images,
            deep_analysis=req.deep_analysis,
        )
        answer = await agent_service.send_chat(
            question=question,
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
        from app.tools.deep_analysis_tool import stash_documents
        stash_documents(None)


# File type classification
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".ico"}
_DOC_EXTS = {".pdf", ".docx", ".xlsx", ".xls", ".pptx"}
_ALLOWED_EXTS = _IMAGE_EXTS | _DOC_EXTS | {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".log", ".html", ".css",
    ".py", ".js", ".ts", ".java", ".go", ".rs", ".c", ".cpp",
    ".sh", ".bat", ".sql", ".vue", ".jsx", ".tsx", ".svg",
}


def _file_category(ext: str) -> str:
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _DOC_EXTS:
        return "document"
    return "text"


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), session_id: str = Form(default="")):
    """Upload a file (image, document, or text) to the session workspace."""
    ext = Path(file.filename or "file.txt").suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required for uploads")
    from app.tools.workspace import create_session_workspace
    workspace_path = create_session_workspace(session_id)

    uploads_dir = workspace_path / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    # Save file with original name (avoid collision with counter)
    safe_name = Path(file.filename or "file").name
    dest = uploads_dir / safe_name
    counter = 1
    while dest.exists():
        dest = uploads_dir / f"{Path(safe_name).stem}_{counter}{Path(safe_name).suffix}"
        counter += 1

    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    category = _file_category(ext)
    url = f"/api/chat/file/{session_id}/{safe_name}"
    return {
        "filename": safe_name,
        "path": str(dest),
        "url": url,
        "type": category,
        "session_id": session_id,
    }


@router.get("/file/{session_id}/{filename:path}")
async def serve_file(session_id: str, filename: str):
    """Serve an uploaded file from the session workspace."""
    from fastapi.responses import FileResponse
    workspace_path = Path(settings.workspaces_base) / session_id
    uploads_dir = workspace_path / "uploads"
    file_path = uploads_dir / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)
