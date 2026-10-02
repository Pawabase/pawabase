"""The storefront's theme, and resolving which store a hostname belongs to.

Two responsibilities that belong together because both answer "what does the
public see":

* :func:`resolve_store` maps a request's `Host` to a store. This is the
  storefront's tenant boundary, and it is a *different* mechanism from the
  dashboard's — the dashboard resolves a tenant from the signed-in user's
  membership, the storefront from the hostname, because a shopper has no
  membership and a merchant's staff should not be able to reach another
  merchant's admin by changing a URL.

* :func:`theme_prop` turns a `Theme` row into the values the storefront React
  app renders with.

Nothing a merchant types is ever evaluated. Colours are matched against a strict
pattern and fonts against a fixed list, because a "customisable" storefront that
accepts arbitrary CSS is a stored-XSS feature with a friendly name.
"""

from __future__ import annotations

import re
from typing import Any

from .. import q

__all__ = [
    "FONTS",
    "ensure_theme",
    "resolve_store",
    "safe_color",
    "theme_prop",
]

#: `#rgb`, `#rrggbb`, `#rrggbbaa`. Nothing else — not `rgb()`, not a named
#: colour, and certainly not `url(...)`.
_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

#: The fonts the theme editor offers. A fixed list rather than free text: the
#: value becomes a Google Fonts URL, and an arbitrary string there is an
#: arbitrary external request from every shopper's browser.
FONTS = (
    "Inter",
    "Instrument Sans",
    "DM Sans",
    "Plus Jakarta Sans",
    "Source Serif 4",
    "Playfair Display",
    "Libre Baskerville",
    "IBM Plex Mono",
)

#: The aesthetic values a theme may carry, and what each may be. Anything not
#: on this list is replaced with the first entry, which is always the neutral
#: one — so an unknown value renders as "no opinion" rather than as nothing.
_STYLE_OPTIONS: dict[str, tuple[str, ...]] = {
    "scale": ("normal", "compact", "display", "poster"),
    "tracking": ("normal", "tight", "wide"),
    "case": ("normal", "upper"),
    "rhythm": ("normal", "tight", "airy", "vast"),
    "edge": ("hairline", "none", "bold"),
    "texture": ("none", "grain", "grid"),
    "measure": ("normal", "narrow", "wide"),
}


def _safe_style(value: Any) -> dict[str, str]:
    """A theme's aesthetic, with every field forced onto its allowlist.

    These reach the page as CSS custom properties and as class names. An
    arbitrary string in either is an arbitrary string in a `style` attribute or
    a `class`, so the set is closed and the fallback is the neutral option.
    """
    stored = value if isinstance(value, dict) else {}
    return {
        field: (
            stored[field]
            if isinstance(stored.get(field), str) and stored[field] in options
            else options[0]
        )
        for field, options in _STYLE_OPTIONS.items()
    }


def safe_color(value: str | None, fallback: str) -> str:
    """A colour, or the fallback. Never whatever the merchant typed."""
    if value and _COLOR.match(value.strip()):
        return value.strip()
    return fallback


def safe_font(value: str | None, fallback: str = "Inter") -> str:
    return value if value in FONTS else fallback


async def resolve_store(db: Any, settings: Any, host: str) -> tuple[q.Row, q.Row | None] | None:
    """The store a hostname belongs to, or ``None``.

    1. A ``domains`` row matching the host exactly; a custom domain must be verified, or anyone could point their DNS at
       the platform and serve another merchant's shop from it.
    2. The platform subdomain ``<slug>.<STOREFRONT_SUFFIX>``, which every store has from creation.

    The port is kept when matching the suffix (in development a shop lives at ``slug.shop.localhost:8000``), and a
    bare-host match is tried second (a reverse proxy may present ``slug.shop.example.com`` while the suffix carries a port).
    """
    hostname = (host or "").strip().lower()
    if not hostname:
        return None
    domain = await q.first(db, "domains", {"hostname": hostname})
    if domain is not None and domain.is_usable:
        store = await q.get(db, "stores", domain.store_id)
        if store is not None:
            return store, domain
    suffix = settings.storefront_suffix.lower()
    for candidate, tail in ((hostname, suffix), (hostname.split(":", 1)[0], suffix.split(":", 1)[0])):
        if candidate.endswith(f".{tail}"):
            store = await q.first(db, "stores", {"slug": candidate[: -len(tail) - 1]})
            if store is not None:
                return store, None
    return None


async def ensure_theme(db: Any, store: q.Row) -> q.Row:
    """The store's active theme, created on first use (a store without a theme is a theme not yet created, not an error state)."""
    theme = await q.first(db, "themes", {"store_id": store.pk, "is_active": True})
    if theme is not None:
        return theme
    return await q.insert(db, "themes", {
        "store_id": store.pk, "name": "Default", "is_active": True,
        "header_links": [{"label": "Shop", "url": "/products"}, {"label": "Collections", "url": "/collections"}],
        "footer_links": [{"label": "Contact", "url": "/pages/contact"}, {"label": "Returns", "url": "/pages/returns"}],
        "seo_title": store.name, "seo_description": f"Shop {store.name}.",
        "color_primary": "#111827", "color_accent": "#2563eb", "color_background": "#ffffff", "color_surface": "#f9fafb",
        "color_text": "#111827", "color_muted": "#6b7280", "color_border": "#e5e7eb", "font_heading": "Inter", "font_body": "Inter",
        "corner_style": "soft", "style": {}, "sections": [], "social_links": {}, "robots_policy": "index,follow"})


def theme_prop(theme: q.Row, store: q.Row) -> dict[str, Any]:
    """The theme, sanitised, as the storefront's props.

    Every colour goes through `safe_color` on the way out, not only on the way
    in: a row written before a validation rule existed, or edited directly in
    the database, must not be able to inject anything into a rendered page.
    """
    return {
        "name": theme.name,
        "logo_url": theme.logo_url,
        "favicon_url": theme.favicon_url,
        "colors": {
            "primary": safe_color(theme.color_primary, "#111827"),
            "accent": safe_color(theme.color_accent, "#2563eb"),
            "background": safe_color(theme.color_background, "#ffffff"),
            "surface": safe_color(theme.color_surface, "#f9fafb"),
            "text": safe_color(theme.color_text, "#111827"),
            "muted": safe_color(theme.color_muted, "#6b7280"),
            "border": safe_color(theme.color_border, "#e5e7eb"),
        },
        "fonts": {
            "heading": safe_font(theme.font_heading),
            "body": safe_font(theme.font_body),
        },
        "corner_style": (
            theme.corner_style if theme.corner_style in ("sharp", "soft", "round") else "soft"
        ),
        # How the template carries itself — type scale, rhythm, edges, texture.
        # Sanitised the same way the colours are: a value from a row written by
        # an older version, or edited straight in the database, must not reach
        # a rendered page.
        "style": _safe_style(theme.style),
        "header_links": theme.header_links or [],
        "footer_links": theme.footer_links or [],
        "social_links": theme.social_links or {},
        "announcement": theme.announcement,
        "footer_text": theme.footer_text,
        "store": {
            "name": store.name,
            "slug": store.slug,
            "currency": store.currency,
            "support_email": store.support_email or store.email,
        },
        "seo": {
            "title": theme.seo_title or store.name,
            "description": theme.seo_description,
            "og_image": theme.og_image_url,
            "robots": theme.robots_policy or "index,follow",
        },
        "help_desk": {
            "enabled": store.help_desk_enabled,
            "greeting": store.help_desk_greeting,
        },
    }
