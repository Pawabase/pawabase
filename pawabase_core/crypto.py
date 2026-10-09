"""Encrypting stored secrets with Sillo's crypto helpers.

:func:`sillo.helpers.crypto.derive_key` turns the platform master key into a
Fernet key (PBKDF2-HMAC-SHA256); :func:`~sillo.helpers.crypto.encrypt` and
:func:`~sillo.helpers.crypto.decrypt` do the rest. The salt is fixed per
installation purpose, so the same master key always derives the same key and
existing secrets stay readable across restarts.

A value can also be *bound to an environment*: sealed under a key derived (HKDF) from the master
key and the environment's name, and written with a ``v2.`` prefix. It opens only for that
environment, so a ciphertext copied from one environment's row into another's is refused instead
of read. Values sealed before this existed have no prefix and open under the master key as ever;
nothing is rewritten unless asked to be.
"""

from __future__ import annotations

import base64
import functools

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sillo.helpers.crypto import decrypt, derive_key, encrypt

SECRET_PREFIX = "secret://"
_SALT = b"pawabase:secrets:v1"
#: Marks a ciphertext sealed for one environment.
BOUND_PREFIX = "v2."


@functools.lru_cache(maxsize=8)
def _fernet_key(master_key: str) -> bytes:
    raw, _ = derive_key(master_key, salt=_SALT, iterations=200_000)
    return base64.urlsafe_b64encode(raw)


@functools.lru_cache(maxsize=512)
def _environment_key(master_key: str, env: str) -> bytes:
    """The Fernet key for one environment, derived from the master key and the environment's name."""
    material = base64.urlsafe_b64decode(_fernet_key(master_key))
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        info=b"pawabase:environment:" + env.encode(),
    ).derive(material)
    return base64.urlsafe_b64encode(derived)


class SecretBox:
    """Encrypts and decrypts secret values under the master key.

    Without an environment, ``seal`` and ``open`` behave as they always have. With one, a sealed
    value is bound to that environment and ``open`` needs the same environment.
    """

    def __init__(self, master_key: str) -> None:
        self._master = master_key
        self._key = _fernet_key(master_key)

    def seal(self, plaintext: str, env: str | None = None) -> str:
        if env is None:
            return encrypt(plaintext, self._key)
        return BOUND_PREFIX + encrypt(plaintext, _environment_key(self._master, env))

    def open(self, ciphertext: str, env: str | None = None) -> str:
        if not ciphertext.startswith(BOUND_PREFIX):
            return decrypt(ciphertext, self._key)  # sealed before environments had keys
        if env is None:
            raise ValueError(
                "this value is sealed for an environment; say which one to open it for"
            )
        return decrypt(ciphertext[len(BOUND_PREFIX) :], _environment_key(self._master, env))

    @staticmethod
    def is_bound(ciphertext: str) -> bool:
        return ciphertext.startswith(BOUND_PREFIX)

    def reseal(self, ciphertext: str, *, source: str, target: str) -> str:
        """The same value, sealed for *target*, for a copy of a definition into another environment."""
        return self.seal(self.open(ciphertext, source), target)


def mask(value: str | None) -> str:
    """A display form that proves a value exists without revealing it."""
    if not value:
        return ""
    return "•" * 8 + (value[-4:] if len(value) > 12 else "")


def is_reference(value: object) -> bool:
    return isinstance(value, str) and value.startswith(SECRET_PREFIX)


def reference_name(value: str) -> str:
    return value[len(SECRET_PREFIX) :]
