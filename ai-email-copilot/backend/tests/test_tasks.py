"""Tests for Tasks API endpoints."""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.db.models import Base, User, Task
from app.db.session import get_db


@pytest.mark.asyncio
async def test_tasks_crud_lifecycle():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        # Create test user
        async with session_factory() as session:
            user = User(email="taskuser@example.com", display_name="Task Tester")
            session.add(user)
            await session.commit()
            await session.refresh(user)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # 1. Create task
            create_res = await ac.post(
                "/api/v1/tasks/?user_email=taskuser@example.com",
                json={"title": "Prepare board report", "status": "pending"},
            )
            assert create_res.status_code == 200
            task_data = create_res.json()
            task_id = task_data["id"]
            assert task_data["title"] == "Prepare board report"
            assert task_data["status"] == "pending"

            # 2. List tasks
            list_res = await ac.get("/api/v1/tasks/?user_email=taskuser@example.com")
            assert list_res.status_code == 200
            tasks = list_res.json()
            assert len(tasks) == 1

            # 3. Update task status to completed
            patch_res = await ac.patch(
                f"/api/v1/tasks/{task_id}?user_email=taskuser@example.com",
                json={"status": "completed"},
            )
            assert patch_res.status_code == 200
            assert patch_res.json()["status"] == "completed"

            # 4. Delete task
            del_res = await ac.delete(
                f"/api/v1/tasks/{task_id}?user_email=taskuser@example.com"
            )
            assert del_res.status_code == 200
            assert del_res.json()["status"] == "deleted"

            # 5. List tasks should now be empty
            list_res2 = await ac.get("/api/v1/tasks/?user_email=taskuser@example.com")
            assert len(list_res2.json()) == 0

    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

