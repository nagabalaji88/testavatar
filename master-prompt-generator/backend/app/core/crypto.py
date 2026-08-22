"""Symmetric encryption for provider credentials held in the database.

A credential entered through the admin UI has to survive a restart, which
means it lands in a durable store. Writing it there in plaintext would make
every database backup, replica and `pg_dump` a copy of the operator's provider
keys, so the value is encrypted with a key that lives outside the database and
is never itself persisted.

The key comes from CREDENTIAL_ENCRYPTION_KEY when set. When it is not, one is
derived from JWT_SECRET_KEY instead, so a fresh deployment can store a
credential without a second secret to configure. That fallback is a
convenience, not a recommendation: it ties the lifetime of every stored
credential to the signing secret, and rotating the signing secret makes them
undecryptable. Both cases are logged at startup so the choice is visible.

Undecryptable ciphertext is reported as such rather than raised through the
call path: the realistic cause is exactly that rotation, and the useful
response is "re-enter this key", not a 500 on the models page.
"""

from __future__ import annotations

import base64
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Domain separation for the derived key. Without it, a future feature deriving
# its own key from the same JWT secret would produce the same bytes, and one
# component's ciphertext would be decryptable by the other.
_HKDF_INFO = b"mpg.provider-credentials.v1"


class CredentialDecryptionError(Exception):
    """Stored ciphertext could not be decrypted with the current key."""


def _derive_fernet_key(secret: str) -> bytes:
    """Stretch an arbitrary-length secret into the 32 bytes Fernet requires."""
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(secret.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


_fernet: Optional[Fernet] = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        configured = (settings.credential_encryption_key or "").strip()
        if configured:
            _fernet = Fernet(_derive_fernet_key(configured))
            logger.info("credential_cipher_ready", extra={"source": "CREDENTIAL_ENCRYPTION_KEY"})
        else:
            _fernet = Fernet(_derive_fernet_key(settings.jwt_secret_key))
            logger.warning(
                "credential_cipher_derived_from_jwt_secret",
                extra={
                    "detail": (
                        "CREDENTIAL_ENCRYPTION_KEY is unset; provider credentials are "
                        "encrypted with a key derived from JWT_SECRET_KEY. Rotating "
                        "that secret will require re-entering every stored key."
                    )
                },
            )
    return _fernet


def reset_cipher_cache() -> None:
    """Drop the memoised cipher. Only used by tests that swap the settings."""
    global _fernet
    _fernet = None


def encrypt_secret(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise CredentialDecryptionError(
            "stored credential could not be decrypted with the current "
            "encryption key; re-enter it"
        ) from exc


def last4(secret: str) -> str:
    """The tail of a key, for confirming *which* key is stored.

    Four characters is enough to tell two keys apart when checking that the
    right one is in place, and short enough not to meaningfully narrow a brute
    force against the remainder.
    """
    trimmed = secret.strip()
    return trimmed[-4:] if len(trimmed) >= 4 else "*" * len(trimmed)
