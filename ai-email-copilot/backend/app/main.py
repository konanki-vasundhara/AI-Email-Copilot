"""AI Email Copilot - FastAPI Application Entry Point."""
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, Any

from fastapi import FastAPI, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db, close_db, init_db
from app.api.auth import router as auth_router
from app.api.emails import router as emails_router
from app.api.tasks import router as tasks_router
from app.api.replies import router as replies_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown."""
    try:
        await init_db()
    except Exception as e:
        logger.warning(f"Could not verify DB initialization: {e}")
    yield
    await close_db()


app = FastAPI(
    title=settings.APP_NAME,
    description="Production-minded AI-powered Gmail productivity assistant with explicit reply confirmation.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix=settings.API_V1_PREFIX)
app.include_router(auth_router)
app.include_router(emails_router, prefix=settings.API_V1_PREFIX)
app.include_router(tasks_router, prefix=settings.API_V1_PREFIX)
app.include_router(replies_router, prefix=settings.API_V1_PREFIX)

frontend_dist = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend", "dist")
)
if os.path.exists(frontend_dist):
    app.mount("/app", StaticFiles(directory=frontend_dist, html=True), name="frontend")


@app.get("/", tags=["General"])
async def root() -> Dict[str, str]:
    """Root endpoint welcoming users and pointing to documentation."""
    return {
        "message": f"Welcome to {settings.APP_NAME}",
        "app": "/app",
        "docs": "/docs",
        "health": "/health",
        "version": "0.1.0",
    }


@app.get("/health", tags=["Health"], status_code=status.HTTP_200_OK)
async def health_check(
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Health check verifying API uptime and live database connectivity."""
    db_status = "healthy"
    db_error = None
    try:
        result = await db.execute(text("SELECT 1"))
        _ = result.scalar()
    except Exception as e:
        db_status = "unhealthy"
        db_error = str(e)

    overall_status = "healthy" if db_status == "healthy" else "degraded"

    return {
        "status": overall_status,
        "application": settings.APP_NAME,
        "version": "0.1.0",
        "database": {
            "status": db_status,
            "error": db_error,
        },
    }

