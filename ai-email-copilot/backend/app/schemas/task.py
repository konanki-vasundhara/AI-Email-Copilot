"""Task schemas."""
from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class TaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    due_at: Optional[datetime] = None
    status: str = "pending"


class TaskEmailSummary(BaseModel):
    id: UUID
    subject: Optional[str] = None
    sender: Optional[str] = None
    received_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskCreate(TaskBase):
    email_id: Optional[UUID] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_at: Optional[datetime] = None
    status: Optional[str] = None


class TaskRead(TaskBase):
    id: UUID
    user_id: UUID
    email_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    email: Optional[TaskEmailSummary] = None

    model_config = ConfigDict(from_attributes=True)

