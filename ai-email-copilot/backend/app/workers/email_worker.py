"""Background Email Worker for periodic recovery sync, watch renewal, and async processing."""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from app.db.session import async_session_factory
from app.db.models import User, OAuthConnection, GmailSyncState
from app.services.gmail_service import gmail_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("email_worker")


async def run_recovery_sync_for_all_users() -> None:
    """
    Periodic recovery synchronization:
    Scans all users with active OAuth connections and synchronizes any missed emails
    or renews watches expiring within 24 hours.
    """
    logger.info("Starting background recovery sync loop...")

    async with async_session_factory() as db:
        # Fetch users with active (non-revoked) OAuth connections
        stmt = (
            select(User)
            .join(OAuthConnection)
            .where(OAuthConnection.revoked_at.is_(None))
            .distinct()
        )
        users = (await db.execute(stmt)).scalars().all()
        logger.info(f"Found {len(users)} active connected users to check.")

        now = datetime.now(timezone.utc)
        for user in users:
            try:
                sync_stmt = select(GmailSyncState).where(GmailSyncState.user_id == user.id)
                sync_state = (await db.execute(sync_stmt)).scalar_one_or_none()

                # Determine if sync is needed (e.g. not synced in last 10 minutes)
                needs_sync = False
                if not sync_state or not sync_state.last_synced_at:
                    needs_sync = True
                elif (now - sync_state.last_synced_at) > timedelta(minutes=10):
                    needs_sync = True

                if needs_sync:
                    logger.info(f"Running sync for user: {user.email}")
                    if sync_state and sync_state.last_history_id:
                        synced = await gmail_service.sync_history(
                            user, sync_state.last_history_id, db
                        )
                    else:
                        synced = await gmail_service.sync_recent_emails(user, db, limit=15)
                    logger.info(f"Successfully synced {len(synced)} messages for {user.email}")

            except Exception as e:
                logger.error(f"Error during background sync for user {user.email}: {e}")

    logger.info("Recovery sync loop completed.")


async def worker_main_loop(interval_seconds: int = 300) -> None:
    """Main worker entry point: runs periodically until interrupted."""
    logger.info(f"Background worker started (interval: {interval_seconds}s)")
    while True:
        try:
            await run_recovery_sync_for_all_users()
        except Exception as e:
            logger.error(f"Worker iteration error: {e}")
        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    asyncio.run(worker_main_loop())

