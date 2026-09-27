"""Encrypting stored secrets with Sillo's crypto helpers.

:func:`sillo.helpers.crypto.derive_key` turns the platform master key into a
Fernet key (PBKDF2-HMAC-SHA256); :func:`~sillo.helpers.crypto.encrypt` and
:func:`~sillo.helpers.crypto.decrypt` do the rest. The salt is fixed per
installation purpose, so the same master key always derives the same key and
existing secrets stay readable across restarts.
"""

from __future__ import annotations

import base64
import functools

from sillo.helpers.crypto import decrypt, derive_key, encrypt

SECRET_PREFIX = "secret://"
_SALT = b"pawabase:secrets:v1"


@functools.lru_cache(maxsize=8)
def _fernet_key(master_key: str) -> bytes:
    raw, _ = derive_key(master_key, salt=_SALT, iterations=200_000)
    return base64.urlsafe_b64encode(raw)


class SecretBox:
    """Encrypts and decrypts secret values under the master key."""

    def __init__(self, master_key: str) -> None:
        self._key = _fernet_key(master_key)

    def seal(self, plaintext: str) -> str:
        return encrypt(plaintext, self._key)

    def open(self, ciphertext: str) -> str:
        return decrypt(ciphertext, self._key)


def mask(value: str | None) -> str:
    """A display form that proves a value exists without revealing it."""
    if not value:
        return ""
    return "•" * 8 + (value[-4:] if len(value) > 12 else "")


def is_reference(value: object) -> bool:
    return isinstance(value, str) and value.startswith(SECRET_PREFIX)


def reference_name(value: str) -> str:
    return value[len(SECRET_PREFIX) :]
