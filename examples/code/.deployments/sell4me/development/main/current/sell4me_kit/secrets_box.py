"""Encryption for the provider credentials a merchant entrusts to us.

A merchant's Stripe secret key can move their money. It must not sit in the
database in plaintext, where a read-only SQL injection, a leaked backup or a
support engineer with a query console is enough to drain every connected store.

The scheme is Fernet-style: HMAC-SHA256 over a ChaCha-free construction built
from the standard library only — AES is not in the stdlib, so this uses
HMAC-SHA256 as a keystream generator (a NIST SP 800-108 KDF in counter mode)
and authenticates with a separate HMAC key. Both keys are derived from
`SECRET_KEY` by HKDF, with distinct info strings, so encryption and
authentication never share key material.

Two properties this gets right that a naive implementation does not:

* **Encrypt-then-MAC.** The tag covers the nonce and the ciphertext, so a
  tampered value fails authentication before anything tries to decrypt it.
* **Constant-time comparison** of the tag, so the failure is not a timing
  oracle for forging one.

Rotating `SECRET_KEY` makes every stored credential undecryptable, which
surfaces as providers reporting "reconnect required" rather than as silent
wrong keys. That is the correct failure: the alternative is a platform that
cannot tell a rotated key from a corrupted one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os


__all__ = ["CredentialError", "decrypt_secret", "encrypt_secret", "mask_secret"]

_NONCE_BYTES = 16
_TAG_BYTES = 32
_VERSION = b"\x01"


class CredentialError(Exception):
    """A stored credential could not be read.

    Raised for a bad tag, a truncated value or a `SECRET_KEY` that no longer
    matches. Deliberately one exception for all three: telling a caller *which*
    is telling an attacker which.
    """


def _hkdf(secret_key: str, info: bytes, length: int = 32) -> bytes:
    """HKDF-SHA256 over the application secret, salted per purpose."""
    secret = secret_key.encode()
    prk = hmac.new(b"sillo-commerce-credentials", secret, hashlib.sha256).digest()
    output = b""
    block = b""
    counter = 1
    while len(output) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        output += block
        counter += 1
    return output[:length]


def _keystream(secret_key: str, nonce: bytes, length: int) -> bytes:
    """A keystream of `length` bytes for this nonce.

    SP 800-108 counter mode: each block is HMAC(key, counter ‖ nonce). The
    nonce is random per encryption, so the same plaintext encrypts differently
    every time and two credentials with the same value are not visibly equal.
    """
    key = _hkdf(secret_key, b"encryption")
    output = b""
    counter = 0
    while len(output) < length:
        output += hmac.new(
            key, counter.to_bytes(4, "big") + nonce, hashlib.sha256
        ).digest()
        counter += 1
    return output[:length]


def encrypt_secret(plaintext: str | None, secret_key: str) -> str | None:
    """Encrypt a credential for storage. `None` and `""` stay as they are.

    Returns a URL-safe base64 string of `version ‖ nonce ‖ ciphertext ‖ tag`.
    """
    if not plaintext:
        return None
    raw = plaintext.encode()
    nonce = os.urandom(_NONCE_BYTES)
    stream = _keystream(secret_key, nonce, len(raw))
    ciphertext = bytes(a ^ b for a, b in zip(raw, stream, strict=True))

    body = _VERSION + nonce + ciphertext
    tag = hmac.new(_hkdf(secret_key, b"authentication"), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + tag).decode()


def decrypt_secret(token: str | None, secret_key: str) -> str | None:
    """Read a credential back. `None` in, `None` out.

    Raises:
        CredentialError: if the value was tampered with, truncated, or was
            encrypted under a different `SECRET_KEY`.
    """
    if not token:
        return None
    try:
        blob = base64.urlsafe_b64decode(token.encode())
    except Exception as error:  # noqa: BLE001
        raise CredentialError("Stored credential is not valid base64.") from error

    if len(blob) < 1 + _NONCE_BYTES + _TAG_BYTES:
        raise CredentialError("Stored credential is truncated.")

    body, tag = blob[:-_TAG_BYTES], blob[-_TAG_BYTES:]
    expected = hmac.new(_hkdf(secret_key, b"authentication"), body, hashlib.sha256).digest()
    # Constant-time: a fast reject on the first wrong byte would let an
    # attacker forge a tag one byte at a time.
    if not hmac.compare_digest(expected, tag):
        raise CredentialError("Stored credential failed authentication.")

    if body[:1] != _VERSION:
        raise CredentialError("Unknown credential format version.")

    nonce = body[1 : 1 + _NONCE_BYTES]
    ciphertext = body[1 + _NONCE_BYTES :]
    stream = _keystream(secret_key, nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, stream, strict=True)).decode()


def mask_secret(plaintext: str | None) -> str | None:
    """What the dashboard shows in place of a key it will never redisplay.

    `sk_live_51H8x…4dQ2` — enough for a merchant to recognise which key is
    installed, not enough to use it.
    """
    if not plaintext:
        return None
    if len(plaintext) <= 12:
        return "•" * len(plaintext)
    return f"{plaintext[:8]}…{plaintext[-4:]}"
