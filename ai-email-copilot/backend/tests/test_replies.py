"""Tests for Reply drafts and strict human approval safeguard."""
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from app.main import app
from app.db.models import Base, User, Email, ReplyDraft, AuditEvent
from app.db.session import get_db
from app.services.gmail_service import gmail_service


@pytest.mark.asyncio
async def test_reply_draft_lifecycle_and_safeguards():
    """Verify approval state machine, explicit confirmation requirement, and duplicate send prevention."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        # Seed user and email
        async with session_factory() as session:
            user = User(email="sender.user@example.com", display_name="Sender User")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            email_rec = Email(
                user_id=user.id,
                gmail_message_id="msg_reply_test_1",
                thread_id="th_reply_test_1",
                subject="Partnership Discussion",
                sender="partner@acme.com",
                snippet="Let us schedule a time next Tuesday.",
                received_at=datetime.now(timezone.utc),
            )
            session.add(email_rec)
            await session.commit()
            await session.refresh(email_rec)
            email_id = str(email_rec.id)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # 1. Generate AI draft
            gen_res = await ac.post(
                f"/api/v1/replies/generate?email_id={email_id}&user_email=sender.user@example.com"
            )
            assert gen_res.status_code == 200
            draft_data = gen_res.json()
            draft_id = draft_data["id"]
            # Strict safeguard: generated draft MUST start in 'pending' status
            assert draft_data["status"] == "pending"
            assert "partner@acme.com" in draft_data["recipient"]

            # 2. User edits draft body
            edit_res = await ac.patch(
                f"/api/v1/replies/{draft_id}?user_email=sender.user@example.com",
                json={"body": "Sounds great! Looking forward to meeting next Tuesday at 2 PM."},
            )
            assert edit_res.status_code == 200
            assert "Tuesday at 2 PM" in edit_res.json()["body"]

            # 3. Safeguard: Attempt approve without explicit confirmation ('confirm': false) -> MUST FAIL
            reject_res = await ac.post(
                f"/api/v1/replies/{draft_id}/approve?user_email=sender.user@example.com",
                json={"confirm": False},
            )
            assert reject_res.status_code == 400
            assert "mandatory" in reject_res.json()["detail"].lower()

            # 4. Approve with explicit confirmation ('confirm': true)
            with patch.object(
                gmail_service, "get_valid_access_token", new_callable=AsyncMock
            ) as mock_token, patch.object(
                gmail_service, "send_email", new_callable=AsyncMock
            ) as mock_send:
                mock_token.return_value = "mock-gmail-access-token"
                mock_send.return_value = {"id": "sent_msg_123"}

                approve_res = await ac.post(
                    f"/api/v1/replies/{draft_id}/approve?user_email=sender.user@example.com",
                    json={"confirm": True},
                )
                assert approve_res.status_code == 200
                sent_data = approve_res.json()
                assert sent_data["status"] == "sent"
                assert sent_data["sent_at"] is not None
                assert sent_data["idempotency_key"] is not None

            # 5. Safeguard: Attempt double-send on already sent draft -> MUST BE REJECTED
            duplicate_res = await ac.post(
                f"/api/v1/replies/{draft_id}/approve?user_email=sender.user@example.com",
                json={"confirm": True},
            )
            assert duplicate_res.status_code == 400
            assert "already been sent" in duplicate_res.json()["detail"].lower()

            # 6. Verify AuditEvent logged in DB
            async with session_factory() as session:
                audit_stmt = select(AuditEvent).where(AuditEvent.event_type == "email.sent")
                audit_rec = (await session.execute(audit_stmt)).scalar_one_or_none()
                assert audit_rec is not None
                assert audit_rec.payload["recipient"] == "partner@acme.com"

    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
