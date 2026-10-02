"""Media: where uploaded files live, and how they are addressed.

One public bucket, ``media``, holds everything a merchant uploads and everything derived from it, on Pawabase
storage (local disk or S3, whichever the installation configured). The key layout is what makes cleanup possible:

    stores/<store id>/products/<product id>/<hash>/original.jpg
                                              /1200.webp  /600.webp  /300.webp  /og.jpg

Every derivative sits beside the original under one hash directory, so deleting an image is deleting a prefix.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

MEDIA_BUCKET = "media"

#: Checked against the *sniffed* type, not the declared one, so renaming ``payload.svg`` to ``photo.jpg`` does not get it in.
#: SVG is absent on purpose: it can carry script, and an SVG served from the shop's origin is a stored-XSS vector.
ACCEPTED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/avif", "image/gif")

_SIGNATURES: tuple[tuple[int, bytes, str], ...] = (
    (0, b"\xff\xd8\xff", "image/jpeg"),
    (0, b"\x89PNG\r\n\x1a\n", "image/png"),
    (0, b"GIF87a", "image/gif"),
    (0, b"GIF89a", "image/gif"),
    (8, b"WEBP", "image/webp"),
    (4, b"ftypavif", "image/avif"),
    (4, b"ftypavis", "image/avif"),
)

DERIVATIVE_NAMES = ("1200.webp", "600.webp", "300.webp", "og.jpg", "1200.jpg", "600.jpg", "300.jpg")


def sniff_image(data: bytes) -> str:
    """What these bytes actually are, regardless of what they were called."""
    for offset, marker, kind in _SIGNATURES:
        if data[offset : offset + len(marker)] == marker:
            return kind
    return "application/octet-stream"


def object_key(*, store_id: int, kind: str, owner_id: int, name: str, digest: str = "") -> str:
    folder = digest or secrets.token_hex(8)
    safe = name.replace("/", "-").replace("..", "-").strip() or "file"
    return f"stores/{store_id}/{kind}/{owner_id}/{folder}/{safe}"


def digest_for(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def prefix_for_image(key: str) -> str:
    return key.rsplit("/", 1)[0]


def media_url(settings: Any, key: str) -> str:
    """Where a browser fetches this object: a configured public origin (a CDN) wins, otherwise the storage API path
    (``<gateway>/storage/v1/object/media/<key>``, to which a client adds its publishable key as ``?apikey=``)."""
    base = getattr(settings, "media_base_url", "") or ""
    if base:
        return f"{base.rstrip('/')}/{key}"
    return f"/storage/v1/object/{MEDIA_BUCKET}/{key}"


def key_from_url(url: str | None) -> str | None:
    if not url:
        return None
    marker = f"/{MEDIA_BUCKET}/"
    if marker in url:
        return url.split(marker, 1)[1].split("?", 1)[0]
    return None


async def write(c: Any, key: str, data: bytes, *, content_type: str = "") -> dict[str, Any]:
    """Write bytes to the media bucket as the application itself (the merchant's permission was already checked on the route)."""
    return await c.runtime.storage_put(MEDIA_BUCKET, key, data, content_type)


async def read(c: Any, key: str, limit: int = 16 * 1024 * 1024) -> bytes:
    return await c.runtime.storage_read(MEDIA_BUCKET, key, limit)


async def delete_image(c: Any, key: str) -> None:
    """Delete an original and every derivative (they share a directory)."""
    folder = prefix_for_image(key)
    for name in (key.rsplit("/", 1)[-1], *DERIVATIVE_NAMES):
        try:
            await c.runtime.storage_delete(MEDIA_BUCKET, f"{folder}/{name}")
        except Exception:  # noqa: BLE001 — a derivative that was never made is not an error
            pass
