"""Reply Drafts and Approval API endpoints with strict human confirmation safeguards."""
import base64
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import List, Optional, Dict, Any
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import User, Email, ReplyDraft, AuditEvent
from app.schemas.reply import (
    ReplyDraftRead,
    ReplyDraftCreate,
    ReplyDraftUpdate,
    ReplyDraftApprove,
)
from app.services.gmail_service import gmail_service
from app.services.ai_service import ai_service

router = APIRouter(prefix="/replies", tags=["Replies & Approval Safeguard"])


@router.get("/", response_model=List[ReplyDraftRead], summary="List reply drafts")
async def list_reply_drafts(
    user_email: str = Query(..., description="User email identifier"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (pending, approved, sending, sent, failed, cancelled)"),
    email_id: Optional[uuid.UUID] = Query(None, description="Filter by related email ID"),
    db: AsyncSession = Depends(get_db),
) -> List[ReplyDraft]:
    """Lists reply drafts pending user review or already processed."""
    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    query = select(ReplyDraft).where(ReplyDraft.user_id == user.id)

    if status_filter:
        query = query.where(ReplyDraft.status == status_filter)
    if email_id:
        query = query.where(ReplyDraft.email_id == email_id)

    query = query.order_by(desc(ReplyDraft.created_at))
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/generate", response_model=ReplyDraftRead, summary="Generate an AI draft reply")
async def generate_draft(
    email_id: uuid.UUID = Query(..., description="ID of the email to generate a reply draft for"),
    user_email: str = Query(..., description="User email for ownership verification"),
    db: AsyncSession = Depends(get_db),
) -> ReplyDraft:
    """
    Generates an AI reply draft for the specified email and saves it as 'pending'.
    Strict safeguard: the draft will NEVER be sent automatically.
    """
    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    e_stmt = select(Email).where(Email.id == email_id, Email.user_id == user.id)
    email_rec = (await db.execute(e_stmt)).scalar_one_or_none()
    if not email_rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email not found")

    # Generate draft using AI service
    tasks = ai_service.extract_tasks_and_deadlines(email_rec.subject or "", email_rec.snippet or "")
    draft_body = ai_service.generate_reply_draft(
        email_rec.subject or "", email_rec.sender or "", email_rec.snippet or "", tasks
    )

    reply_subject = (
        email_rec.subject
        if (email_rec.subject or "").lower().startswith("re:")
        else f"Re: {email_rec.subject}"
    )

    draft = ReplyDraft(
        user_id=user.id,
        email_id=email_rec.id,
        thread_id=email_rec.thread_id,
        recipient=email_rec.sender or "",
        subject=reply_subject,
        body=draft_body,
        status="pending",
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


@router.patch("/{draft_id}", response_model=ReplyDraftRead, summary="Edit reply draft body")
async def update_reply_draft(
    draft_id: uuid.UUID,
    payload: ReplyDraftUpdate,
    user_email: str = Query(..., description="User email for ownership verification"),
    db: AsyncSession = Depends(get_db),
) -> ReplyDraft:
    """Allows user to edit draft text or cancel before approval."""
    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    d_stmt = select(ReplyDraft).where(ReplyDraft.id == draft_id, ReplyDraft.user_id == user.id)
    draft = (await db.execute(d_stmt)).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reply draft not found")

    if draft.status == "sent":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot edit a draft that has already been sent"
        )

    if payload.body is not None:
        draft.body = payload.body
    if payload.status is not None:
        draft.status = payload.status

    await db.commit()
    await db.refresh(draft)
    return draft


@router.post("/{draft_id}/approve", response_model=ReplyDraftRead, summary="Explicit user approval & sending")
async def approve_and_send_reply(
    draft_id: uuid.UUID,
    payload: ReplyDraftApprove,
    user_email: str = Query(..., description="User email for ownership verification"),
    db: AsyncSession = Depends(get_db),
) -> ReplyDraft:
    """
    CRITICAL HUMAN-IN-THE-LOOP SAFEGUARD:
    1. Requires explicit user confirmation (confirm=True).
    2. Validates user ownership.
    3. Idempotently prevents double-sends using draft state machine.
    4. Delivers reply via Gmail API using authorized user connection.
    5. Logs immutable AuditEvent.
    """
    if not payload.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Explicit user confirmation ('confirm': true) is mandatory to send an email.",
        )

    # 1. Verify User and Draft ownership
    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    d_stmt = select(ReplyDraft).where(ReplyDraft.id == draft_id, ReplyDraft.user_id == user.id)
    draft = (await db.execute(d_stmt)).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reply draft not found")

    # 2. Strict status check to prevent race conditions & double-sending
    if draft.status == "sent":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This reply has already been sent."
        )
    if draft.status == "sending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This reply is currently being dispatched. Duplicate send prevented.",
        )

    # Apply any final user edit submitted alongside the approval click
    if payload.edited_body is not None:
        draft.body = payload.edited_body

    # Transition to 'sending' and set idempotency key
    idempotency_key = str(uuid.uuid4())
    draft.status = "sending"
    draft.idempotency_key = idempotency_key
    draft.approved_at = datetime.now(timezone.utc)
    await db.commit()

    # 3. Deliver via Gmail API
    try:
        access_token = await gmail_service.get_valid_access_token(user.id, db)
        await gmail_service.send_email(
            access_token=access_token,
            sender_email=user.email,
            recipient=draft.recipient,
            subject=draft.subject,
            body=draft.body,
            thread_id=draft.thread_id,
        )

        # Update draft to sent
        draft.status = "sent"
        draft.sent_at = datetime.now(timezone.utc)
        draft.send_error = None

        # Log audit event
        audit = AuditEvent(
            user_id=user.id,
            event_type="email.sent",
            payload={
                "draft_id": str(draft.id),
                "recipient": draft.recipient,
                "subject": draft.subject,
                "thread_id": draft.thread_id,
                "idempotency_key": idempotency_key,
            },
        )
        db.add(audit)
        await db.commit()
        await db.refresh(draft)
        return draft

    except Exception as e:
        draft.status = "failed"
        draft.send_error = str(e)

        audit_fail = AuditEvent(
            user_id=user.id,
            event_type="email.send_failed",
            payload={
                "draft_id": str(draft.id),
                "recipient": draft.recipient,
                "error": str(e),
                "idempotency_key": idempotency_key,
            },
        )
        db.add(audit_fail)
        await db.commit()
        await db.refresh(draft)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to dispatch email through Gmail: {e}",
        )
