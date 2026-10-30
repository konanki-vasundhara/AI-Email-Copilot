"""Google OAuth 2.0 service for authorization, token exchange, and refresh."""
import hmac
import hashlib
import time
import secrets
from typing import Dict, Any, List, Optional
from urllib.parse import urlencode
import httpx

from app.config import settings


class OAuthService:
    GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
    GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

    SCOPES: List[str] = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]

    def __init__(self):
        self.client_id = settings.GOOGLE_CLIENT_ID
        self.client_secret = settings.GOOGLE_CLIENT_SECRET
        self.redirect_uri = settings.GOOGLE_REDIRECT_URI

    def generate_state(self, user_hint: Optional[str] = None) -> str:
        """
        Generates a tamper-proof CSRF state token containing a random nonce,
        timestamp, and HMAC signature using the token encryption key.
        """
        nonce = secrets.token_hex(16)
        ts = int(time.time())
        hint = user_hint or "anonymous"
        payload = f"{nonce}:{ts}:{hint}"
        sig = hmac.new(
            settings.TOKEN_ENCRYPTION_KEY.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{payload}:{sig}"

    def verify_state(self, state: str, max_age_seconds: int = 900) -> Dict[str, Any]:
        """
        Validates the state token's HMAC signature and checks expiration (default: 15 min).
        Returns the parsed payload or raises ValueError.
        """
        parts = state.split(":")
        if len(parts) != 4:
            raise ValueError("Invalid state token format")

        nonce, ts_str, hint, received_sig = parts
        try:
            ts = int(ts_str)
        except ValueError:
            raise ValueError("Invalid timestamp in state token")

        # Verify age
        if time.time() - ts > max_age_seconds:
            raise ValueError("State token has expired. Please initiate login again.")

        # Verify HMAC
        payload = f"{nonce}:{ts_str}:{hint}"
        expected_sig = hmac.new(
            settings.TOKEN_ENCRYPTION_KEY.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(received_sig, expected_sig):
            raise ValueError("State token signature mismatch (possible CSRF attack)")

        return {"nonce": nonce, "timestamp": ts, "hint": hint}

    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """Constructs Google OAuth authorization URL requesting offline access."""
        if not self.client_id:
            raise ValueError("GOOGLE_CLIENT_ID is not configured in environment")

        state_val = state or self.generate_state()
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.SCOPES),
            "access_type": "offline",
            "prompt": "consent",  # Ensures Google returns a refresh token
            "state": state_val,
        }
        return f"{self.GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code_for_tokens(self, code: str) -> Dict[str, Any]:
        """Exchanges authorization code with Google for access & refresh tokens."""
        if not self.client_secret:
            raise ValueError("GOOGLE_CLIENT_SECRET is not configured in environment")

        data = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(self.GOOGLE_TOKEN_URL, data=data)
            if response.status_code != 200:
                raise ValueError(
                    f"Token exchange failed ({response.status_code}): {response.text}"
                )
            return response.json()

    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Fetches the user's Google profile (email, name, sub)."""
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(self.GOOGLE_USERINFO_URL, headers=headers)
            if response.status_code != 200:
                raise ValueError(
                    f"Fetching userinfo failed ({response.status_code}): {response.text}"
                )
            return response.json()

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Uses a refresh token to retrieve a fresh access token."""
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(self.GOOGLE_TOKEN_URL, data=data)
            if response.status_code != 200:
                raise ValueError(
                    f"Token refresh failed ({response.status_code}): {response.text}"
                )
            return response.json()

    async def revoke_token(self, token: str) -> bool:
        """Revokes an OAuth token with Google servers."""
        data = {"token": token}
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(self.GOOGLE_REVOKE_URL, data=data)
            return response.status_code == 200


oauth_service = OAuthService()

