"""Tests for Google OAuth 2.0 and token encryption."""
import time
import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.crypto_service import CryptoService, crypto_service
from app.services.oauth_service import OAuthService, oauth_service
from app.db.models import Base, User, OAuthConnection, AuditEvent
from app.db.session import get_db
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select


def test_crypto_encryption_decryption():
    """Verify symmetric encryption round-trip and tamper detection."""
    cs = CryptoService("sample-32-byte-secret-for-fernet-key-1234567890")
    secret_token = "1//04ABCDEF123456789_refresh_token_example"

    encrypted = cs.encrypt(secret_token)
    assert encrypted != secret_token

    decrypted = cs.decrypt(encrypted)
    assert decrypted == secret_token

    # Tampered token should raise ValueError
    with pytest.raises(ValueError, match="Token decryption failed"):
        cs.decrypt(encrypted[:-5] + "XXXXX")


def test_oauth_state_generation_and_verification():
    """Verify CSRF state token tamper-proofing and expiration checks."""
    state = oauth_service.generate_state(user_hint="user123")
    assert state is not None

    parsed = oauth_service.verify_state(state)
    assert parsed["hint"] == "user123"
    assert "nonce" in parsed

    # Tampering with payload should fail verification
    tampered_state = "tampered" + state[8:]
    with pytest.raises(ValueError, match="signature mismatch"):
        oauth_service.verify_state(tampered_state)

    # Expired state should fail
    with pytest.raises(ValueError, match="expired"):
        oauth_service.verify_state(state, max_age_seconds=-1)


@pytest.mark.asyncio
async def test_google_login_endpoint():
    """Verify login endpoint returns valid authorization URL with scopes."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/api/v1/auth/google/login")

    assert response.status_code == 200
    data = response.json()
    assert "authorization_url" in data
    url = data["authorization_url"]
    assert "accounts.google.com" in url
    assert "gmail.readonly" in url
    assert "gmail.send" in url
    assert "state=" in url


@pytest.mark.asyncio
async def test_google_callback_and_disconnect():
    """Verify end-to-end OAuth callback, encrypted persistence, and disconnect."""
    # Use isolated test database
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        # Generate valid CSRF state
        valid_state = oauth_service.generate_state()

        # Mock external Google HTTP calls
        mock_tokens = {
            "access_token": "mock-access-token-xyz",
            "refresh_token": "mock-refresh-token-secret-123",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send",
        }
        mock_userinfo = {
            "sub": "google-sub-99999",
            "email": "test.user@example.com",
            "name": "Test User",
        }

        with patch.object(
            oauth_service, "exchange_code_for_tokens", new_callable=AsyncMock
        ) as mock_exchange, patch.object(
            oauth_service, "get_user_info", new_callable=AsyncMock
        ) as mock_info:
            mock_exchange.return_value = mock_tokens
            mock_info.return_value = mock_userinfo

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                # 1. Trigger callback
                cb_res = await ac.get(
                    f"/api/v1/auth/google/callback?code=mock_code_123&state={valid_state}"
                )
                assert cb_res.status_code == 200
                cb_data = cb_res.json()
                assert cb_data["status"] == "connected"
                assert cb_data["user"]["email"] == "test.user@example.com"
                assert cb_data["connection"]["has_refresh_token"] is True

                # 2. Check status endpoint
                st_res = await ac.get(
                    "/api/v1/auth/status?email=test.user@example.com"
                )
                assert st_res.status_code == 200
                st_data = st_res.json()
                assert st_data["connected"] is True
                assert st_data["has_refresh_token"] is True

                # 3. Disconnect
                with patch.object(
                    oauth_service, "revoke_token", new_callable=AsyncMock
                ) as mock_revoke:
                    mock_revoke.return_value = True

                    dc_res = await ac.post(
                        "/api/v1/auth/google/disconnect?email=test.user@example.com"
                    )
                    assert dc_res.status_code == 200
                    dc_data = dc_res.json()
                    assert dc_data["status"] == "disconnected"

                # 4. Status should now report disconnected
                st_res2 = await ac.get(
                    "/api/v1/auth/status?email=test.user@example.com"
                )
                assert st_res2.status_code == 200
                assert st_res2.json()["connected"] is False

    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

