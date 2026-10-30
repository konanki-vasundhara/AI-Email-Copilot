"""Tests for root and health check endpoints."""
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_root_endpoint():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "AI Email Copilot" in data["message"]
    assert data["docs"] == "/docs"
    assert data["health"] == "/health"


@pytest.mark.asyncio
async def test_health_endpoint():
    from app.db.session import get_db
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["application"] == "AI Email Copilot"
        assert data["version"] == "0.1.0"
        assert data["database"]["status"] == "healthy"
    finally:
        app.dependency_overrides.clear()
        await test_engine.dispose()

