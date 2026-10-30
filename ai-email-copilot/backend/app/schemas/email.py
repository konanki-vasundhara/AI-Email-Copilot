"""Email and Analysis schemas."""
from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class EmailAnalysisRead(BaseModel):
    id: UUID
    email_id: UUID
    category: str
    importance: str
    summary: str
    sentiment: Optional[str] = None
    model_id: str
    raw_entities: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())


class EmailRead(BaseModel):
    id: UUID
    user_id: UUID
    gmail_message_id: str
    thread_id: str
    subject: Optional[str] = None
    sender: Optional[str] = None
    snippet: Optional[str] = None
    received_at: datetime
    created_at: datetime
    analysis: Optional[EmailAnalysisRead] = None

    model_config = ConfigDict(from_attributes=True)

