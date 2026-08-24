"""AES-256-GCM encryption for journal contents at rest.

Blob format: base64(nonce[12] || ciphertext || tag[16]), with the row's
user_id bound as AAD so ciphertexts cannot be replayed across users.
"""

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.settings import Settings

NONCE_BYTES = 12


class JournalCryptoError(RuntimeError):
    """Raised when journal encryption or decryption cannot be performed."""


class JournalCipher:
    def __init__(self, settings: Settings) -> None:
        self._key: bytes | None = None
        if settings.journal_encryption_key:
            try:
                key = base64.b64decode(settings.journal_encryption_key, validate=True)
            except (ValueError, TypeError) as exc:
                raise JournalCryptoError("JOURNAL_ENCRYPTION_KEY is not valid base64") from exc
            if len(key) != 32:
                raise JournalCryptoError("JOURNAL_ENCRYPTION_KEY must decode to exactly 32 bytes")
            self._key = key

    @property
    def enabled(self) -> bool:
        return self._key is not None

    def encrypt_fields(self, payload: dict, user_id: str) -> str:
        if self._key is None:
            raise JournalCryptoError("Encryption is not configured")
        nonce = os.urandom(NONCE_BYTES)
        plaintext = json.dumps(payload, separators=(",", ":")).encode()
        ciphertext = AESGCM(self._key).encrypt(nonce, plaintext, user_id.encode())
        return base64.b64encode(nonce + ciphertext).decode()

    def decrypt_blob(self, blob: str, user_id: str) -> dict:
        if self._key is None:
            raise JournalCryptoError("Encryption is not configured; cannot decrypt journal data")
        try:
            raw = base64.b64decode(blob)
            if len(raw) <= NONCE_BYTES:
                raise ValueError("blob too short")
            nonce, ciphertext = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
            plaintext = AESGCM(self._key).decrypt(nonce, ciphertext, user_id.encode())
            parsed = json.loads(plaintext)
        except Exception as exc:
            raise JournalCryptoError(
                "Failed to decrypt journal data (wrong or rotated JOURNAL_ENCRYPTION_KEY?)"
            ) from exc
        if not isinstance(parsed, dict):
            raise JournalCryptoError("Decrypted journal payload is malformed")
        return parsed
