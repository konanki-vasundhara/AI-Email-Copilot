"""Tests for Gmail service and email endpoints."""
import base64
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.db.models import Base, User, OAuthConnection, Email, EmailAnalysis
from app.db.session import get_db
from app.services.gmail_service import gmail_service


def test_parse_message_payload():
    """Verify MIME decoding and body extraction logic."""
    raw_body = "Hello! Please review the attached contract by Friday."
    b64_body = base64.urlsafe_b64encode(raw_body.encode("utf-8")).decode("utf-8")

    sample_gmail_msg = {
        "id": "18f9c1e2b4a5d6e7",
        "threadId": "18f9c1e2b4a5d6e7",
        "snippet": "Hello! Please review the attached contract...",
        "internalDate": "1715000000000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": "Contract Review Due Friday"},
                {"name": "From", "value": "partner@acme.com"},
                {"name": "To", "value": "user@example.com"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": b64_body},
                }
            ],
        },
    }

    parsed = gmail_service.parse_message_payload(sample_gmail_msg)
    assert parsed["gmail_message_id"] == "18f9c1e2b4a5d6e7"
    assert parsed["subject"] == "Contract Review Due Friday"
    assert parsed["sender"] == "partner@acme.com"
    assert parsed["body_text"] == raw_body


@pytest.mark.asyncio
async def test_email_api_endpoints():
    """Verify listing and viewing email endpoints with database integration."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        # Seed test user and email
        async with session_factory() as session:
            user = User(email="test.client@example.com", display_name="Client User")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            email_rec = Email(
                user_id=user.id,
                gmail_message_id="msg_001",
                thread_id="th_001",
                subject="Sprint Planning Tomorrow",
                sender="lead@example.com",
                snippet="Let's discuss backlog...",
                received_at=datetime.now(timezone.utc),
            )
            session.add(email_rec)
            await session.commit()
            await session.refresh(email_rec)
            email_id = str(email_rec.id)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # 1. List emails
            res = await ac.get("/api/v1/emails/?user_email=test.client@example.com")
            assert res.status_code == 200
            items = res.json()
            assert len(items) == 1
            assert items[0]["subject"] == "Sprint Planning Tomorrow"

            # 2. Get single email
            res_single = await ac.get(
                f"/api/v1/emails/{email_id}?user_email=test.client@example.com"
            )
            assert res_single.status_code == 200
            assert res_single.json()["gmail_message_id"] == "msg_001"

            # 3. Ownership verification check (different user)
            res_forbidden = await ac.get(
                f"/api/v1/emails/{email_id}?user_email=stranger@example.com"
            )
            assert res_forbidden.status_code == 404

    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

