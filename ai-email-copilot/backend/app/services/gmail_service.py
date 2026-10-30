"""Gmail API Service for fetching messages, decoding MIME content, and syncing history."""
import base64
import email
from email import policy
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import httpx

from app.services.oauth_service import oauth_service
from app.services.crypto_service import crypto_service
from app.db.models import User, OAuthConnection, Email, GmailSyncState
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class GmailService:
    BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"

    async def get_valid_access_token(
        self, user_id: Any, db: AsyncSession
    ) -> str:
        """
        Retrieves a valid access token for the given user.
        If the access token is near expiration, decrypts the refresh token
        and refreshes with Google automatically.
        """
        stmt = select(OAuthConnection).where(
            OAuthConnection.user_id == user_id,
            OAuthConnection.revoked_at.is_(None),
        )
        result = await db.execute(stmt)
        conn = result.scalar_one_or_none()

        if not conn:
            raise ValueError("No active Gmail connection found for this user")

        if not conn.encrypted_refresh_token:
            raise ValueError("No refresh token stored. Re-authorization required.")

        # Decrypt refresh token and fetch new access token
        plain_refresh_token = crypto_service.decrypt(conn.encrypted_refresh_token)
        token_data = await oauth_service.refresh_access_token(plain_refresh_token)
        access_token = token_data.get("access_token")

        if not access_token:
            raise ValueError("Failed to obtain fresh access token from Google")

        return access_token

    async def list_messages(
        self,
        access_token: str,
        query: Optional[str] = None,
        max_results: int = 20,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetches a list of Gmail message IDs and thread IDs."""
        params: Dict[str, Any] = {"maxResults": max_results}
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token

        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.get(f"{self.BASE_URL}/messages", headers=headers, params=params)
            if res.status_code != 200:
                raise ValueError(f"Gmail list messages failed ({res.status_code}): {res.text}")
            return res.json()

    async def get_message(
        self, access_token: str, message_id: str
    ) -> Dict[str, Any]:
        """Fetches full details of a specific email message."""
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.get(
                f"{self.BASE_URL}/messages/{message_id}",
                headers=headers,
                params={"format": "full"},
            )
            if res.status_code != 200:
                raise ValueError(f"Gmail get message failed ({res.status_code}): {res.text}")
            return res.json()

    def parse_message_payload(self, raw_msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parses raw Gmail API message response into a structured, sanitized dict:
        subject, sender, recipient, date, snippet, body_text.
        """
        gmail_id = raw_msg.get("id")
        thread_id = raw_msg.get("threadId")
        snippet = raw_msg.get("snippet", "")
        internal_date_ms = int(raw_msg.get("internalDate", 0))

        if internal_date_ms > 0:
            received_at = datetime.fromtimestamp(internal_date_ms / 1000.0, tz=timezone.utc)
        else:
            received_at = datetime.now(timezone.utc)

        headers_list = raw_msg.get("payload", {}).get("headers", [])
        headers_map = {h.get("name", "").lower(): h.get("value", "") for h in headers_list}

        subject = headers_map.get("subject", "(No Subject)")
        sender = headers_map.get("from", "")
        recipient = headers_map.get("to", "")

        body_text = self._extract_body(raw_msg.get("payload", {}))
        if not body_text:
            body_text = snippet

        return {
            "gmail_message_id": gmail_id,
            "thread_id": thread_id,
            "subject": subject,
            "sender": sender,
            "recipient": recipient,
            "snippet": snippet,
            "received_at": received_at,
            "body_text": body_text,
        }

    def _extract_body(self, payload: Dict[str, Any]) -> str:
        """Recursively traverses MIME parts to extract plain text body."""
        mime_type = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data")

        if mime_type == "text/plain" and body_data:
            return self._decode_base64url(body_data)

        # Check sub-parts (e.g. multipart/alternative, multipart/mixed)
        parts = payload.get("parts", [])
        for part in parts:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return self._decode_base64url(part["body"]["data"])

        # Fallback to HTML converted to text if no text/plain is available
        for part in parts:
            if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                return self._decode_base64url(part["body"]["data"])
            if part.get("parts"):
                nested = self._extract_body(part)
                if nested:
                    return nested

        return ""

    def _decode_base64url(self, data_str: str) -> str:
        """Decodes URL-safe base64 string handling padding."""
        try:
            # Replace URL-safe chars and add necessary padding
            padded = data_str.replace("-", "+").replace("_", "/")
            padded += "=" * ((4 - len(padded) % 4) % 4)
            decoded_bytes = base64.b64decode(padded)
            return decoded_bytes.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""

    async def sync_recent_emails(
        self, user: User, db: AsyncSession, limit: int = 15
    ) -> List[Email]:
        """
        Synchronizes the most recent emails for a user:
        1. Gets fresh access token.
        2. Lists recent messages.
        3. Retrieves and parses full message payloads.
        4. Saves new Email records idempotently.
        """
        access_token = await self.get_valid_access_token(user.id, db)
        list_res = await self.list_messages(access_token, max_results=limit)
        messages_meta = list_res.get("messages", [])

        synced_emails = []
        for meta in messages_meta:
            msg_id = meta.get("id")
            if not msg_id:
                continue

            # Idempotency check: verify if already stored
            stmt = select(Email).where(
                Email.user_id == user.id,
                Email.gmail_message_id == msg_id,
            )
            existing = (await db.execute(stmt)).scalar_one_or_none()
            if existing:
                synced_emails.append(existing)
                continue

            # Fetch full details
            raw_msg = await self.get_message(access_token, msg_id)
            parsed = self.parse_message_payload(raw_msg)

            email_record = Email(
                user_id=user.id,
                gmail_message_id=parsed["gmail_message_id"],
                thread_id=parsed["thread_id"],
                subject=parsed["subject"],
                sender=parsed["sender"],
                snippet=parsed["snippet"],
                received_at=parsed["received_at"],
            )
            db.add(email_record)
            await db.flush()

            # Trigger AI analysis, task extraction, and draft suggestion
            from app.services.ai_service import ai_service
            try:
                await ai_service.analyze_email_and_save(email_record, parsed["body_text"], db)
            except Exception:
                pass  # Do not block sync if individual analysis encounters an issue

            synced_emails.append(email_record)

        # Update sync state
        sync_stmt = select(GmailSyncState).where(GmailSyncState.user_id == user.id)
        sync_state = (await db.execute(sync_stmt)).scalar_one_or_none()
        if sync_state:
            sync_state.last_synced_at = datetime.now(timezone.utc)
            sync_state.sync_status = "idle"

        await db.commit()
        return synced_emails

    async def send_email(
        self,
        access_token: str,
        sender_email: str,
        recipient: str,
        subject: str,
        body: str,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sends an RFC 2822 email message using Gmail API."""
        msg = email.message.EmailMessage()
        msg["To"] = recipient
        msg["From"] = sender_email
        msg["Subject"] = subject
        msg.set_content(body)

        raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        payload: Dict[str, Any] = {"raw": raw_b64}
        if thread_id:
            payload["threadId"] = thread_id

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=25.0) as client:
            res = await client.post(
                f"{self.BASE_URL}/messages/send",
                headers=headers,
                json=payload,
            )
            if res.status_code != 200:
                raise ValueError(f"Gmail send failed ({res.status_code}): {res.text}")
            return res.json()

    def decode_pubsub_payload(self, message_data_b64: str) -> Dict[str, Any]:
        """Decodes Google Cloud Pub/Sub push notification payload."""
        import json
        try:
            raw_bytes = base64.b64decode(message_data_b64)
            return json.loads(raw_bytes.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Invalid Pub/Sub message payload: {e}")

    async def setup_push_watch(
        self, user: User, topic_name: str, db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Registers Google Cloud Pub/Sub topic to receive real-time push notifications.
        Requires topic to grant publish rights to gmail-api-push@system.gserviceaccount.com.
        """
        access_token = await self.get_valid_access_token(user.id, db)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "topicName": topic_name,
            "labelIds": ["INBOX"],
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(f"{self.BASE_URL}/watch", headers=headers, json=payload)
            if res.status_code != 200:
                raise ValueError(f"Gmail watch setup failed ({res.status_code}): {res.text}")

            data = res.json()
            # Update sync state with expiration and historyId
            sync_stmt = select(GmailSyncState).where(GmailSyncState.user_id == user.id)
            sync_state = (await db.execute(sync_stmt)).scalar_one_or_none()
            if sync_state:
                exp_ms = int(data.get("expiration", 0))
                if exp_ms > 0:
                    sync_state.watch_expiration = datetime.fromtimestamp(exp_ms / 1000.0, tz=timezone.utc)
                if data.get("historyId"):
                    sync_state.last_history_id = str(data["historyId"])
                await db.commit()

            return data

    async def sync_history(
        self, user: User, start_history_id: str, db: AsyncSession
    ) -> List[Email]:
        """
        Incrementally synchronizes only mailbox changes since start_history_id.
        Falls back to full recent sync if historyId has expired.
        """
        access_token = await self.get_valid_access_token(user.id, db)
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"startHistoryId": start_history_id}

        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.get(f"{self.BASE_URL}/history", headers=headers, params=params)

            # If history ID is too old / invalid, Google returns 404
            if res.status_code == 404:
                return await self.sync_recent_emails(user, db, limit=15)

            if res.status_code != 200:
                raise ValueError(f"Gmail history query failed ({res.status_code}): {res.text}")

            history_data = res.json()

        history_records = history_data.get("history", [])
        new_emails = []

        for record in history_records:
            added_messages = record.get("messagesAdded", [])
            for item in added_messages:
                msg_meta = item.get("message", {})
                msg_id = msg_meta.get("id")
                if not msg_id:
                    continue

                # Idempotency check
                stmt = select(Email).where(
                    Email.user_id == user.id, Email.gmail_message_id == msg_id
                )
                if (await db.execute(stmt)).scalar_one_or_none():
                    continue

                raw_msg = await self.get_message(access_token, msg_id)
                parsed = self.parse_message_payload(raw_msg)

                email_record = Email(
                    user_id=user.id,
                    gmail_message_id=parsed["gmail_message_id"],
                    thread_id=parsed["thread_id"],
                    subject=parsed["subject"],
                    sender=parsed["sender"],
                    snippet=parsed["snippet"],
                    received_at=parsed["received_at"],
                )
                db.add(email_record)
                await db.flush()

                # Trigger AI analysis
                from app.services.ai_service import ai_service
                try:
                    await ai_service.analyze_email_and_save(email_record, parsed["body_text"], db)
                except Exception:
                    pass

                new_emails.append(email_record)

        # Update cursor in sync state
        latest_history_id = history_data.get("historyId")
        if latest_history_id:
            sync_stmt = select(GmailSyncState).where(GmailSyncState.user_id == user.id)
            sync_state = (await db.execute(sync_stmt)).scalar_one_or_none()
            if sync_state:
                sync_state.last_history_id = str(latest_history_id)
                sync_state.last_synced_at = datetime.now(timezone.utc)
                sync_state.sync_status = "idle"

        await db.commit()
        return new_emails


gmail_service = GmailService()


