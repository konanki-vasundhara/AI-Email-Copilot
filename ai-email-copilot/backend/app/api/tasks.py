"""Task Management API endpoints."""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import User, Task
from app.schemas.task import TaskRead, TaskCreate, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.get("/", response_model=List[TaskRead], summary="List user tasks")
async def list_tasks(
    user_email: str = Query(..., description="User email identifier"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (pending, completed, dismissed)"),
    email_id: Optional[uuid.UUID] = Query(None, description="Filter by related email ID"),
    db: AsyncSession = Depends(get_db),
) -> List[Task]:
    """Retrieves all tasks extracted from emails or manually created."""
    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    query = select(Task).where(Task.user_id == user.id)

    if status_filter:
        query = query.where(Task.status == status_filter)
    if email_id:
        query = query.where(Task.email_id == email_id)

    query = query.order_by(desc(Task.created_at))
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/", response_model=TaskRead, summary="Create a new task")
async def create_task(
    payload: TaskCreate,
    user_email: str = Query(..., description="User email identifier"),
    db: AsyncSession = Depends(get_db),
) -> Task:
    """Manually creates a new task."""
    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    task = Task(
        user_id=user.id,
        email_id=payload.email_id,
        title=payload.title,
        description=payload.description,
        due_at=payload.due_at,
        status=payload.status,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@router.patch("/{task_id}", response_model=TaskRead, summary="Update task")
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    user_email: str = Query(..., description="User email for ownership verification"),
    db: AsyncSession = Depends(get_db),
) -> Task:
    """Updates status (e.g. completed, dismissed) or details of an existing task."""
    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id)
    task = (await db.execute(stmt)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if payload.title is not None:
        task.title = payload.title
    if payload.description is not None:
        task.description = payload.description
    if payload.due_at is not None:
        task.due_at = payload.due_at
    if payload.status is not None:
        task.status = payload.status

    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/{task_id}", summary="Delete a task")
async def delete_task(
    task_id: uuid.UUID,
    user_email: str = Query(..., description="User email for ownership verification"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Deletes a task."""
    u_stmt = select(User).where(User.email == user_email)
    user = (await db.execute(u_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id)
    task = (await db.execute(stmt)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    await db.delete(task)
    await db.commit()
    return {"status": "deleted", "task_id": str(task_id)}

