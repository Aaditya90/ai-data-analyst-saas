"""
Symmetric encryption for anything we must store but never want to see in
plaintext at rest — right now, that's DataConnection passwords.

Uses Fernet (AES-128-CBC + HMAC, from the `cryptography` package). The key
must be a 32-byte urlsafe-base64 string; generate one with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from functools import lru_cache

from cryptography.fernet import Fernet

from app.core.config import get_settings


class EncryptionConfigError(RuntimeError):
    """Raised when DATA_ENCRYPTION_KEY is missing — fails loudly rather
    than silently storing something unencrypted or crashing obscurely."""


@lru_cache
def _get_fernet() -> Fernet:
    settings = get_settings()
    if not settings.data_encryption_key:
        raise EncryptionConfigError(
            "DATA_ENCRYPTION_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"\n'
            "and add it to your .env before creating any data connections."
        )
    return Fernet(settings.data_encryption_key.encode())


def encrypt_secret(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
