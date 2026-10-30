"""Cryptographic service for encrypting and decrypting sensitive OAuth tokens."""
import base64
from cryptography.fernet import Fernet, InvalidToken
from app.config import settings


class CryptoService:
    def __init__(self, key: str | None = None):
        raw_key = key or settings.TOKEN_ENCRYPTION_KEY
        # Ensure key is valid 32-byte base64url encoded for Fernet
        try:
            self._fernet = Fernet(raw_key.encode("utf-8"))
        except (ValueError, TypeError):
            # Fallback / normalization: pad or truncate to 32 bytes and base64url encode
            padded = raw_key.encode("utf-8").ljust(32, b"0")[:32]
            b64_key = base64.urlsafe_b64encode(padded)
            self._fernet = Fernet(b64_key)

    def encrypt(self, plain_text: str) -> str:
        """Encrypts a plaintext string (e.g. refresh token) returning URL-safe base64 ciphertext."""
        if not plain_text:
            return ""
        return self._fernet.encrypt(plain_text.encode("utf-8")).decode("utf-8")

    def decrypt(self, cipher_text: str) -> str:
        """Decrypts ciphertext back to plaintext. Raises ValueError if invalid/tampered."""
        if not cipher_text:
            return ""
        try:
            return self._fernet.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Token decryption failed: invalid or tampered ciphertext") from exc


crypto_service = CryptoService()

