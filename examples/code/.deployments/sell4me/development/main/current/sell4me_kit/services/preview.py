"""Signed preview links.

A merchant previewing an unpublished page is on a *different hostname* from
the dashboard they clicked from — `northwind.shop.localhost` rather than
`localhost`, and on a custom domain something with no relationship to us at
all. The session cookie does not travel there. It cannot be made to on a
custom domain, which is the case that matters: a merchant's own domain will
never share a cookie with our dashboard.

So the proof travels in the link. A token names one store, expires, and is
signed with the application's secret — which gives the three properties
preview needs:

* **It works anywhere.** Platform subdomain, custom domain, a colleague's
  phone. There is no session to be missing.
* **It cannot be forged.** Changing the store id or the expiry invalidates the
  signature, so a shopper cannot mint themselves a look at someone's drafts.
* **It stops working.** An hour, by default. A preview link pasted into a
  public channel leaks a draft for an afternoon rather than forever, which is
  the difference between an embarrassment and an incident.

What it deliberately does *not* do is authenticate anybody. It says "the
bearer may see this store's drafts for the next hour" and nothing else — no
session, no identity, no ability to change anything. That is the whole
security model, and it is small enough to hold in your head.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


__all__ = ["DEFAULT_TTL", "mint", "verify"]

#: How long a preview link lasts. Long enough to check a page on a phone and
#: show someone; short enough that a link pasted somewhere public expires
#: before it matters.
DEFAULT_TTL = 3600

_VERSION = "p1"


def mint(store_id: int, secret_key: str, ttl: int = DEFAULT_TTL) -> str:
    """A preview token for one store.

    The expiry is inside the signed payload rather than checked against a
    stored record: a token that carries its own deadline needs no table, no
    cleanup and no lookup on a storefront request, and there is nothing to
    revoke because there is nothing that lasts.
    """
    expires = int(time.time()) + max(60, ttl)
    payload = f"{_VERSION}.{store_id}.{expires}"
    return f"{payload}.{_sign(payload, secret_key)}"


def verify(token: str | None, secret_key: str) -> int | None:
    """The store this token grants a look at, or nothing.

    Every failure returns `None` rather than raising or distinguishing itself.
    A caller that could tell "expired" from "wrong signature" from "malformed"
    would be a caller that leaks whether a guessed store id exists.
    """
    if not token:
        return None

    parts = token.split(".")
    if len(parts) != 4:
        return None

    version, raw_store, raw_expires, signature = parts
    if version != _VERSION:
        return None

    payload = f"{version}.{raw_store}.{raw_expires}"
    # Constant time: a byte-by-byte comparison leaks how much of a forged
    # signature was right, which is enough to construct the rest.
    if not hmac.compare_digest(signature, _sign(payload, secret_key)):
        return None

    try:
        store_id = int(raw_store)
        expires = int(raw_expires)
    except ValueError:
        return None

    if expires < int(time.time()):
        return None
    return store_id


def _sign(payload: str, secret_key: str) -> str:
    digest = hmac.new(
        secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).digest()
    # URL-safe and unpadded, because this lives in a query string and `=` there
    # is a needless encoding question.
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
