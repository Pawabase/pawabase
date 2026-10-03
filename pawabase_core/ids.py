"""ULIDs: sortable, URL-safe record ids.

A ULID is 26 characters of Crockford base32: a 48-bit millisecond timestamp
followed by 80 random bits. Unlike an integer sequence it does not reveal how
many records exist or let anyone guess the next one; unlike a UUIDv4 it sorts
by creation time, so a primary-key index stays append-mostly and ``ORDER BY id``
means "oldest first". Ids made in the same millisecond still increase.
"""

from __future__ import annotations

import os
import re
import threading
import time

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # no I, L, O or U
#: Matches a ULID in either case; the first character is at most 7, so the timestamp fits 48 bits.
ULID_PATTERN = r"^[0-7][0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{25}$"
_ULID = re.compile(ULID_PATTERN)
_MAX_RANDOM = (1 << 80) - 1

_lock = threading.Lock()
_last_ms = -1
_last_random = 0


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_ulid(now_ms: int | None = None) -> str:
    """A new ULID. Strictly increasing within a process, even inside one millisecond."""
    global _last_ms, _last_random
    with _lock:
        ms = int(time.time() * 1000) if now_ms is None else now_ms
        if ms > _last_ms:
            _last_ms, _last_random = ms, int.from_bytes(os.urandom(10), "big")
        else:
            # Same millisecond (or the clock stepped back): count up from the last id.
            _last_random += 1
            if _last_random > _MAX_RANDOM:
                _last_ms, _last_random = _last_ms + 1, 0
        return _encode(_last_ms, 10) + _encode(_last_random, 16)


def is_ulid(value: object) -> bool:
    """Whether *value* is a well-formed ULID (either case)."""
    return isinstance(value, str) and _ULID.match(value) is not None


def ulid_timestamp_ms(value: str) -> int:
    """The creation time encoded in a ULID, in milliseconds since the epoch."""
    if not is_ulid(value):
        raise ValueError(f"not a ULID: {value!r}")
    number = 0
    for char in value[:10].upper():
        number = number * 32 + ALPHABET.index(char)
    return number
