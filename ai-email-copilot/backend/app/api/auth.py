"""Authentication and Google OAuth API endpoints."""
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import User, OAuthConnection, GmailSyncState, AuditEvent
from app.services.oauth_service import oauth_service
from app.services.crypto_service import crypto_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication & OAuth"])


@router.get(
    "/google/login",
    summary="Initiate Google OAuth flow",
    response_model=None,
)
async def google_login(
    redirect: bool = Query(False, description="Whether to directly redirect to Google consent screen")
) -> Dict[str, str] | RedirectResponse:
    """
    Returns the Google OAuth consent URL with CSRF protection,
    or redirects directly if requested.
    """
    try:
        auth_url = oauth_service.get_authorization_url()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )

    if redirect:
        return RedirectResponse(url=auth_url)

    return {"authorization_url": auth_url}


@router.get("/google/callback", summary="Google OAuth callback handler")
async def google_callback(
    request: Request,
    code: Optional[str] = Query(None, description="Google authorization code"),
    state: Optional[str] = Query(None, description="CSRF state parameter"),
    error: Optional[str] = Query(None, description="Google OAuth error message"),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Handles Google OAuth redirect:
    1. Validates CSRF state token.
    2. Exchanges code for tokens.
    3. Retrieves user profile.
    4. Encrypts and securely stores the refresh token in PostgreSQL.
    5. Creates or updates user record, sync state, and audit log.
    """
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Google OAuth authorization error: {error}",
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required 'code' or 'state' parameter",
        )

    # 1. Verify CSRF State
    try:
        oauth_service.verify_state(state)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"State verification failed: {e}",
        )

    # 2. Exchange code for tokens
    try:
        token_data = await oauth_service.exchange_code_for_tokens(code)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)
    granted_scopes = token_data.get("scope", "").split()

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google token endpoint did not return an access token",
        )

    # 3. Retrieve user profile
    try:
        user_info = await oauth_service.get_user_info(access_token)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    google_sub = user_info.get("sub")
    email = user_info.get("email")
    name = user_info.get("name")

    if not google_sub or not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to retrieve Google user identity",
        )

    # 4. Upsert User in PostgreSQL
    try:
        stmt = select(User).where(User.email == email)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                email=email,
                display_name=name,
                status="active",
            )
            db.add(user)
            await db.flush()
        else:
            if name and not user.display_name:
                user.display_name = name
                await db.flush()

        # 5. Encrypt refresh token (if returned)
        encrypted_rt = None
        if refresh_token:
            encrypted_rt = crypto_service.encrypt(refresh_token)

        # 6. Upsert OAuthConnection
        conn_stmt = select(OAuthConnection).where(
            OAuthConnection.user_id == user.id,
            OAuthConnection.google_subject == google_sub,
        )
        conn_result = await db.execute(conn_stmt)
        oauth_conn = conn_result.scalar_one_or_none()

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        if not oauth_conn:
            oauth_conn = OAuthConnection(
                user_id=user.id,
                google_subject=google_sub,
                encrypted_refresh_token=encrypted_rt,
                granted_scopes=granted_scopes,
                expires_at=expires_at,
                revoked_at=None,
            )
            db.add(oauth_conn)
        else:
            if encrypted_rt:
                oauth_conn.encrypted_refresh_token = encrypted_rt
            oauth_conn.granted_scopes = granted_scopes
            oauth_conn.expires_at = expires_at
            oauth_conn.revoked_at = None

        # 7. Initialize GmailSyncState if not present
        sync_stmt = select(GmailSyncState).where(GmailSyncState.user_id == user.id)
        sync_result = await db.execute(sync_stmt)
        sync_state = sync_result.scalar_one_or_none()

        if not sync_state:
            sync_state = GmailSyncState(
                user_id=user.id,
                sync_status="idle",
            )
            db.add(sync_state)

        # 8. Record AuditEvent
        audit = AuditEvent(
            user_id=user.id,
            event_type="oauth.connected",
            payload={
                "google_sub": google_sub,
                "email": email,
                "scopes": granted_scopes,
                "has_refresh_token": bool(refresh_token or (oauth_conn and oauth_conn.encrypted_refresh_token)),
            },
        )
        db.add(audit)

        await db.commit()
    except Exception as exc:
        logger.error(f"Error persisting Google OAuth connection for {email}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error during OAuth save: {exc}",
        )

    accept_header = request.headers.get("accept", "")
    if "text/html" in accept_header:
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Gmail Connected - AI Email Copilot</title>
                <meta charset="utf-8">
                <style>
                    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 80vh; background: #f8fafc; color: #1e293b; margin: 0; }}
                    .box {{ text-align: center; background: white; padding: 2.5rem 3rem; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.06); border: 1px solid #e2e8f0; max-width: 420px; }}
                    .check {{ width: 48px; height: 48px; background: #dcfce7; color: #16a34a; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 24px; margin: 0 auto 1rem; }}
                    h2 {{ margin: 0 0 0.5rem; color: #0f172a; font-size: 1.25rem; }}
                    p {{ color: #64748b; font-size: 0.95rem; line-height: 1.5; }}
                    .btn {{ display: inline-block; margin-top: 1.25rem; background: #2563eb; color: white; padding: 0.6rem 1.25rem; border-radius: 6px; text-decoration: none; font-weight: 500; font-size: 0.9rem; }}
                </style>
            </head>
            <body>
                <div class="box">
                    <div class="check">&#10003;</div>
                    <h2>Gmail Connected Successfully!</h2>
                    <p>Connected account: <strong>{email}</strong>.<br/>Redirecting to your dashboard...</p>
                    <a id="dash-link" href="http://localhost:5173/?connected=true&email={email}" class="btn">Return to App</a>
                    <script>
                        try {{
                            localStorage.setItem('ai_copilot_email', '{email}');
                        }} catch (e) {{}}
                        // Determine target port (use origin if opened from app or fallback to 5173)
                        var target = 'http://localhost:5173/?connected=true&email=' + encodeURIComponent('{email}');
                        setTimeout(function() {{
                            window.location.href = target;
                        }}, 700);
                    </script>
                </div>
            </body>
            </html>
            """
        )

    return {
        "status": "connected",
        "message": "Gmail account connected securely.",
        "user": {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
        },
        "connection": {
            "google_subject": google_sub,
            "granted_scopes": granted_scopes,
            "expires_at": expires_at.isoformat(),
            "has_refresh_token": bool(oauth_conn.encrypted_refresh_token),
        },
    }


@router.get("/status", summary="Check Gmail OAuth connection status")
async def auth_status(
    email: Optional[str] = Query(None, description="User email to check connection status for"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Returns the current connection and scope status for a user, or active account if email not specified."""
    user = None
    if email and email.strip() and email != "test.user@example.com":
        stmt = select(User).where(User.email == email.strip())
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

    if not user:
        # Auto-discover active connected user from database
        active_pair = (
            await db.execute(
                select(OAuthConnection, User)
                .join(User, OAuthConnection.user_id == User.id)
                .where(OAuthConnection.revoked_at.is_(None))
                .order_by(OAuthConnection.created_at.desc())
            )
        ).first()
        if active_pair:
            oauth_conn, user = active_pair
            return {
                "connected": True,
                "user_id": str(user.id),
                "email": user.email,
                "display_name": user.display_name,
                "granted_scopes": oauth_conn.granted_scopes,
                "has_refresh_token": bool(oauth_conn.encrypted_refresh_token),
                "expires_at": oauth_conn.expires_at.isoformat() if oauth_conn.expires_at else None,
            }
        return {"connected": False, "reason": "No active OAuth connection"}

    conn_stmt = select(OAuthConnection).where(
        OAuthConnection.user_id == user.id,
        OAuthConnection.revoked_at.is_(None),
    )
    conn_result = await db.execute(conn_stmt)
    oauth_conn = conn_result.scalar_one_or_none()

    if not oauth_conn:
        return {"connected": False, "reason": "No active OAuth connection for this email"}

    has_refresh = bool(oauth_conn.encrypted_refresh_token)

    return {
        "connected": True,
        "user_id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "granted_scopes": oauth_conn.granted_scopes,
        "has_refresh_token": has_refresh,
        "expires_at": oauth_conn.expires_at.isoformat() if oauth_conn.expires_at else None,
    }


@router.post("/google/disconnect", summary="Disconnect Gmail and revoke tokens")
async def google_disconnect(
    email: str = Query(..., description="User email to disconnect"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Revokes Google tokens, marks connection as revoked, and logs audit event."""
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    conn_stmt = select(OAuthConnection).where(
        OAuthConnection.user_id == user.id,
        OAuthConnection.revoked_at.is_(None),
    )
    conn_result = await db.execute(conn_stmt)
    oauth_conn = conn_result.scalar_one_or_none()

    if not oauth_conn:
        return {"status": "already_disconnected", "message": "No active connection to disconnect"}

    # Attempt to revoke token with Google if we have an encrypted token
    if oauth_conn.encrypted_refresh_token:
        try:
            plain_rt = crypto_service.decrypt(oauth_conn.encrypted_refresh_token)
            await oauth_service.revoke_token(plain_rt)
        except Exception:
            pass  # Even if remote revocation fails, revoke locally

    # Update connection record
    oauth_conn.revoked_at = datetime.now(timezone.utc)
    oauth_conn.encrypted_refresh_token = None

    # Record AuditEvent
    audit = AuditEvent(
        user_id=user.id,
        event_type="oauth.disconnected",
        payload={"email": user.email, "google_sub": oauth_conn.google_subject},
    )
    db.add(audit)

    await db.commit()

    return {
        "status": "disconnected",
        "message": "Gmail account disconnected and credentials purged successfully.",
    }


@router.delete("/user/delete-data", summary="Purge all user data and disconnect")
async def delete_user_data(
    email: str = Query(..., description="User email whose data must be purged"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    CRITICAL PRIVACY & DELETION SAFEGUARD:
    Completely purges all user records from PostgreSQL:
    - Emails, AI analyses, tasks, reply drafts (via cascading delete).
    - Gmail sync cursors and watch states.
    - Revokes and purges encrypted OAuth credentials.
    - Removes User identity.
    """
    stmt = select(User).where(User.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Attempt remote revocation of stored refresh tokens
    conn_stmt = select(OAuthConnection).where(OAuthConnection.user_id == user.id)
    conns = (await db.execute(conn_stmt)).scalars().all()
    for conn in conns:
        if conn.encrypted_refresh_token:
            try:
                plain_rt = crypto_service.decrypt(conn.encrypted_refresh_token)
                await oauth_service.revoke_token(plain_rt)
            except Exception:
                pass

    # Delete User (cascades to all user-owned data in PostgreSQL)
    await db.delete(user)
    await db.commit()

    return {
        "status": "deleted",
        "message": f"All application data and Gmail credentials for {email} have been permanently deleted.",
    }


@router.get("/audit", summary="Get user security audit events")
async def get_audit_trail(
    email: str = Query(..., description="User email"),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Retrieves immutable audit trail for security, sync, and send events."""
    stmt = select(User).where(User.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    a_stmt = (
        select(AuditEvent)
        .where(AuditEvent.user_id == user.id)
        .order_by(AuditEvent.created_at.desc())
        .limit(50)
    )
    events = (await db.execute(a_stmt)).scalars().all()
    return [
        {
            "id": str(e.id),
            "event_type": e.event_type,
            "payload": e.payload,
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ]
