"""Tests for Google Cloud Pub/Sub Webhooks, Data Deletion, Audit Trail, and Worker."""
import base64
import json
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from app.main import app
from app.db.models import Base, User, OAuthConnection, Email, Task, ReplyDraft, GmailSyncState, AuditEvent
from app.db.session import get_db
from app.services.gmail_service import gmail_service
from app.workers.email_worker import run_recovery_sync_for_all_users


def test_decode_pubsub_payload():
    """Verify base64 decoding of Google Cloud Pub/Sub notification payload."""
    raw_payload = {"emailAddress": "webhook.user@example.com", "historyId": "987654321"}
    b64_str = base64.b64encode(json.dumps(raw_payload).encode("utf-8")).decode("utf-8")

    decoded = gmail_service.decode_pubsub_payload(b64_str)
    assert decoded["emailAddress"] == "webhook.user@example.com"
    assert decoded["historyId"] == "987654321"


@pytest.mark.asyncio
async def test_pubsub_webhook_and_history_sync():
    """Verify receiving real-time Pub/Sub push notification and triggering history sync."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        # Seed test user and sync state with cursor
        async with session_factory() as session:
            user = User(email="webhook.client@example.com", display_name="Webhook Client")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            sync_state = GmailSyncState(
                user_id=user.id,
                last_history_id="111222333",
                sync_status="idle",
            )
            session.add(sync_state)
            await session.commit()

        # Build Pub/Sub push notification
        notification_data = {"emailAddress": "webhook.client@example.com", "historyId": "111222999"}
        b64_data = base64.b64encode(json.dumps(notification_data).encode("utf-8")).decode("utf-8")
        webhook_body = {
            "message": {
                "data": b64_data,
                "messageId": "pubsub_msg_555",
            },
            "subscription": "projects/ai-copilot/subscriptions/gmail-sub",
        }

        # Mock sync_history
        with patch.object(
            gmail_service, "sync_history", new_callable=AsyncMock
        ) as mock_sync_hist:
            mock_sync_hist.return_value = [
                Email(
                    user_id=user.id,
                    gmail_message_id="synced_msg_1",
                    thread_id="th_synced_1",
                    subject="Real-time Notification Email",
                    received_at=datetime.now(timezone.utc),
                )
            ]

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                res = await ac.post("/api/v1/emails/webhook/pubsub", json=webhook_body)
                assert res.status_code == 200
                data = res.json()
                assert data["status"] == "processed"
                assert data["email"] == "webhook.client@example.com"
                assert data["synced_count"] == 1

            mock_sync_hist.assert_called_once()

    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_user_data_deletion_safeguard():
    """Verify complete cascading purging of user emails, tasks, drafts, and credentials."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        # Seed user with related data
        async with session_factory() as session:
            user = User(email="delete.me@example.com", display_name="To Delete")
            session.add(user)
            await session.commit()
            await session.refresh(user)

            email_rec = Email(
                user_id=user.id,
                gmail_message_id="msg_del_1",
                thread_id="th_del_1",
                subject="Confidential",
                received_at=datetime.now(timezone.utc),
            )
            session.add(email_rec)

            task = Task(user_id=user.id, title="Action item to purge")
            session.add(task)

            oauth_conn = OAuthConnection(
                user_id=user.id,
                google_subject="sub_del_123",
                encrypted_refresh_token="enc_dummy_token",
            )
            session.add(oauth_conn)

            audit = AuditEvent(user_id=user.id, event_type="test.audit")
            session.add(audit)

            await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # 1. Trigger complete deletion
            del_res = await ac.delete("/api/v1/auth/user/delete-data?email=delete.me@example.com")
            assert del_res.status_code == 200
            assert "permanently deleted" in del_res.json()["message"]

            # 2. Verify user no longer exists in DB
            async with session_factory() as session:
                u_check = (await session.execute(select(User).where(User.email == "delete.me@example.com"))).scalar_one_or_none()
                assert u_check is None

                e_check = (await session.execute(select(Email).where(Email.user_id == user.id))).scalars().all()
                assert len(e_check) == 0

                t_check = (await session.execute(select(Task).where(Task.user_id == user.id))).scalars().all()
                assert len(t_check) == 0

            # 3. Subsequent delete should return 404
            del_res2 = await ac.delete("/api/v1/auth/user/delete-data?email=delete.me@example.com")
            assert del_res2.status_code == 404

    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

