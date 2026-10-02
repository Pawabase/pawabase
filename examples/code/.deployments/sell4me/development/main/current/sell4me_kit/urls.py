"""Where a store's public shop lives, and the signed link that previews it."""

from __future__ import annotations

from typing import Any

from .services import preview


def storefront_url(settings: Any, store: Any, path: str = "", *, preview_link: bool = False) -> str:
    """The shop's address: a subdomain of ``STOREFRONT_SUFFIX`` (which every store has from creation).

    ``preview_link`` signs the link so it opens drafts, and opens at all before the store has launched. Needed on every link out of the
    dashboard: the shop is a different hostname, so the session that would otherwise prove membership never arrives. A trailing slash
    when there is no path: ``host:8000?preview=…`` is legal and confuses enough clients to be worth avoiding.
    """
    base = f"{settings.shop_url(store.slug)}{path or '/'}"
    if not preview_link:
        return base
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}preview={preview.mint(store.pk, settings.secret_key)}"
