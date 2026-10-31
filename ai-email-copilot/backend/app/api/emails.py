"""Email retrieval and synchronization API endpoints."""
import uuid
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import User, Email, EmailAnalysis, Task, ReplyDraft
from app.schemas.email import EmailRead
from app.services.gmail_service import gmail_service

router = APIRouter(prefix="/emails", tags=["Emails"])


@router.get("/", response_model=List[EmailRead], summary="List stored emails")
async def list_emails(
    user_email: str = Query(..., description="User email identifier"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    category: Optional[str] = Query(None, description="Filter by AI category"),
    folder: Optional[str] = Query(None, description="Filter by folder: 'inbox' (received) or 'sent'"),
    search: Optional[str] = Query(None, description="Search query across subject and sender"),
    db: AsyncSession = Depends(get_db),
) -> List[Email]:
    """Retrieves paginated emails with their AI analysis."""
    # Find user
    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    query = (
        select(Email)
        .options(selectinload(Email.analysis))
        .where(Email.user_id == user.id)
    )

    if folder == "inbox":
        query = query.where(~Email.sender.ilike(f"%{user.email}%"))
    elif folder == "sent":
        query = query.where(Email.sender.ilike(f"%{user.email}%"))

    if search:
        query = query.where(
            (Email.subject.ilike(f"%{search}%")) | (Email.sender.ilike(f"%{search}%"))
        )

    if category:
        query = query.join(Email.analysis).where(EmailAnalysis.category.ilike(category))

    query = query.order_by(desc(Email.received_at)).offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{email_id}", response_model=EmailRead, summary="Get email details")
async def get_email_details(
    email_id: uuid.UUID,
    user_email: str = Query(..., description="User email for ownership verification"),
    db: AsyncSession = Depends(get_db),
) -> Email:
    """Fetches full email details and analysis with strict user ownership verification."""
    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    stmt = (
        select(Email)
        .options(selectinload(Email.analysis))
        .where(Email.id == email_id, Email.user_id == user.id)
    )
    email_record = (await db.execute(stmt)).scalar_one_or_none()
    if not email_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email not found")

    return email_record


@router.post("/sync", summary="Trigger Gmail synchronization")
async def trigger_email_sync(
    user_email: str = Query(..., description="User email to sync inbox for"),
    limit: int = Query(50, ge=1, le=100, description="Max recent messages to sync"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Manually triggers synchronization of recent emails from Gmail."""
    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:
        synced = await gmail_service.sync_recent_emails(user, db, limit=limit)
        return {
            "status": "success",
            "message": f"Successfully synchronized {len(synced)} emails.",
            "count": len(synced),
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/watch", summary="Register Google Cloud Pub/Sub watch for inbox")
async def register_watch(
    user_email: str = Query(..., description="User email"),
    topic_name: str = Query(..., description="Full Google Cloud Pub/Sub topic name: projects/<id>/topics/<name>"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Registers Gmail to push change notifications to a Google Cloud Pub/Sub topic."""
    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:
        watch_res = await gmail_service.setup_push_watch(user, topic_name, db)
        return {"status": "watch_registered", "details": watch_res}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/webhook/pubsub", summary="Google Cloud Pub/Sub webhook receiver")
async def pubsub_webhook(
    body: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Receives push notifications from Google Cloud Pub/Sub:
    1. Validates and decodes the notification payload.
    2. Identifies user and history cursor.
    3. Runs incremental history synchronization and triggers AI analysis.
    """
    message_wrapper = body.get("message")
    if not message_wrapper or "data" not in message_wrapper:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Pub/Sub push format: missing message.data",
        )

    b64_data = message_wrapper["data"]
    try:
        data = gmail_service.decode_pubsub_payload(b64_data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    user_email = data.get("emailAddress")
    if not user_email:
        return {"status": "ignored", "reason": "No emailAddress in notification"}

    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        return {"status": "ignored", "reason": f"No user registered with email {user_email}"}

    # Query sync state for history cursor
    from app.db.models import GmailSyncState
    sync_stmt = select(GmailSyncState).where(GmailSyncState.user_id == user.id)
    sync_state = (await db.execute(sync_stmt)).scalar_one_or_none()

    if sync_state and sync_state.last_history_id:
        synced = await gmail_service.sync_history(user, sync_state.last_history_id, db)
    else:
        synced = await gmail_service.sync_recent_emails(user, db, limit=10)

    return {
        "status": "processed",
        "email": user_email,
        "synced_count": len(synced),
    }

