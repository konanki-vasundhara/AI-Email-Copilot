"""Tests for database models, constraints, and relationships."""
import uuid
import pytest
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

import pytest_asyncio
from app.db.models import (
    Base,
    User,
    OAuthConnection,
    Email,
    EmailAnalysis,
    Task,
    ReplyDraft,
    GmailSyncState,
    AuditEvent,
)


@pytest_asyncio.fixture
async def test_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_user_and_relationships(test_session: AsyncSession):
    # 1. Create User
    user = User(
        email="testuser@example.com",
        display_name="Test User",
        status="active",
    )
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    assert user.id is not None
    assert user.email == "testuser@example.com"

    # 2. Add OAuth Connection
    oauth_conn = OAuthConnection(
        user_id=user.id,
        google_subject="google-sub-12345",
        encrypted_refresh_token="enc_sample_token",
        granted_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    test_session.add(oauth_conn)

    # 3. Add Email & Analysis
    email = Email(
        user_id=user.id,
        gmail_message_id="msg_abc123",
        thread_id="th_xyz789",
        subject="Project Update Q3",
        sender="colleague@example.com",
        snippet="Here is the summary of deliverables...",
        received_at=datetime.now(timezone.utc),
    )
    test_session.add(email)
    await test_session.commit()
    await test_session.refresh(email)

    analysis = EmailAnalysis(
        email_id=email.id,
        category="Work",
        importance="High",
        summary="Action needed on Q3 deliverables before Friday.",
        model_id="facebook/bart-large-cnn",
        raw_entities={"deadline": "Friday"},
    )
    test_session.add(analysis)

    # 4. Add Task
    task = Task(
        user_id=user.id,
        email_id=email.id,
        title="Review Q3 deliverables report",
        status="pending",
    )
    test_session.add(task)

    # 5. Add Reply Draft (Milestone 5 safeguard state)
    draft = ReplyDraft(
        user_id=user.id,
        email_id=email.id,
        thread_id=email.thread_id,
        recipient="colleague@example.com",
        subject="Re: Project Update Q3",
        body="Thanks, I will review by end of day.",
        status="pending",
    )
    test_session.add(draft)

    # 6. Add Audit Event
    audit = AuditEvent(
        user_id=user.id,
        event_type="test.event",
        payload={"action": "test_verification"},
    )
    test_session.add(audit)

    await test_session.commit()

    # Query back to verify relationships
    stmt = select(User).where(User.id == user.id)
    result = await test_session.execute(stmt)
    loaded_user = result.scalar_one()

    assert loaded_user.email == "testuser@example.com"
