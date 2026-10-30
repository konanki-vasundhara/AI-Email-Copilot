"""Reply Draft schemas."""
from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class ReplyDraftBase(BaseModel):
    thread_id: str
    recipient: str
    subject: str
    body: str


class ReplyDraftCreate(ReplyDraftBase):
    email_id: Optional[UUID] = None


class ReplyDraftUpdate(BaseModel):
    body: Optional[str] = None
    status: Optional[str] = None


class ReplyDraftApprove(BaseModel):
    confirm: bool = True
    edited_body: Optional[str] = None


class ReplyDraftRead(ReplyDraftBase):
    id: UUID
    user_id: UUID
    email_id: Optional[UUID] = None
    status: str
    approved_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    send_error: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

