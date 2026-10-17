"""Explicit empty folders in a bucket, backed by a marker object.

Storage has no directories. A prefix exists only while a key lives under it, and
Sillo's ``normalise`` strips a trailing slash, so ``photos/`` cannot be written
as a key on its own. An empty folder is therefore one tiny marker object named
after it: every listing filters the marker back out, and the folder is real
because a key sits under the prefix.

The marker is deliberately **not** dot-prefixed. Sillo's local driver skips any
file whose name starts with ``.`` when it lists (so sidecars stay hidden), which
means a dot-prefixed marker would be invisible to the very prefix derivation
that makes a folder exist — the trick would defeat itself. The marker is
filtered from every listing instead, which works on all three drivers.
"""

from __future__ import annotations

from sillo.storage.paths import normalise

#: The object that makes an empty folder exist. Every listing hides it.
MARKER = "pawabase.folder"

#: What is written into a marker. The bytes are never served; a small body keeps
#: providers that dislike zero-byte objects happy.
MARKER_BODY = b"pawabase-folder\n"


def clean(prefix: str) -> str:
    """A folder path without its trailing slash: ``docs/`` -> ``docs``.

    Raises:
        UnsafeKey: If the path is empty, absolute or otherwise unsafe.
    """
    return normalise(prefix).rstrip("/")


def marker_key(prefix: str) -> str:
    """The marker object's key for a folder. ``docs`` -> ``docs/pawabase.folder``.

    Raises:
        UnsafeKey: If the folder path is empty or unsafe.
    """
    root = clean(prefix)
    return f"{root}/{MARKER}" if root else MARKER


def is_marker(key: str) -> bool:
    """Whether an object key is a folder marker rather than a file to show."""
    return key == MARKER or key.endswith(f"/{MARKER}")


__all__ = ["MARKER", "MARKER_BODY", "clean", "is_marker", "marker_key"]
