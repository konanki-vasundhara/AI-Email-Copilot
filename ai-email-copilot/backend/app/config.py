"""Application Configuration."""
from functools import lru_cache
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    APP_NAME: str = "AI Email Copilot"
    API_V1_PREFIX: str = "/api/v1"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///ai_email_copilot.db"
    DATABASE_URL_SYNC: str = "sqlite:///ai_email_copilot.db"

    # Token Encryption (Fernet 32 url-safe base64 encoded bytes)
    TOKEN_ENCRYPTION_KEY: str = "dGhpcy1pcy1hLXRlc3Qta2V5LTMyLWJ5dGVzLWxvbmctISE="

    # Google OAuth
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Hugging Face
    HF_API_TOKEN: Optional[str] = None
    HF_MODEL_CLASSIFIER: str = "facebook/bart-large-mnli"
    HF_MODEL_SUMMARIZER: str = "facebook/bart-large-cnn"

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE) if ENV_FILE.exists() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

