"""The legal documents an environment's people are shown and asked to accept.

``{"documents": [{"key", "label", "url", "required"}], "version": "2026-10-01", "notice": "…"}``. A *required* document is a box the
person ticks at sign-up (and may not skip, on the hosted page or the sign-up API); every document is linked in the footer of the hosted
screens. What was accepted is recorded on the person (``app_metadata.consent``: which documents, which version, when), so a change of
terms can be answered with "who accepted what". The older ``terms_url`` and ``privacy_url`` of the branding are read as two required
documents when no document list is set.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
MAX_DOCUMENTS = 8
PRESETS = {"terms": "Terms of Service", "privacy": "Privacy Policy"}


def _https(value: str) -> bool:
    parts = urlsplit(value)
    return (parts.scheme == "https" and bool(parts.netloc)) or (
        parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")
    )


def clean(raw: Any, *, strict: bool = False) -> dict[str, Any]:
    """The legal settings that may be stored. ``strict`` raises ``ValueError`` listing every problem; otherwise bad entries are dropped."""
    source = raw if isinstance(raw, dict) else {}
    problems: list[str] = []
    out: dict[str, Any] = {}
    if "documents" in source:
        documents: list[dict[str, Any]] = []
        seen: set[str] = set()
        items = source["documents"]
        if not isinstance(items, list):
            problems.append("documents must be a list")
            items = []
        for item in items[:MAX_DOCUMENTS]:
            if not isinstance(item, dict):
                problems.append("each document must be an object")
                continue
            key, label, url = item.get("key"), str(item.get("label") or "").strip(), item.get("url")
            if not isinstance(key, str) or not SLUG.match(key) or key in seen:
                problems.append(
                    f"document key {key!r} must be unique lowercase letters, digits, - or _"
                )
                continue
            if not label or len(label) > 60:
                problems.append(f"document {key} needs a label of up to 60 characters")
                continue
            if not isinstance(url, str) or len(url) > 2048 or not _https(url):
                problems.append(f"document {key} needs an https address")
                continue
            seen.add(key)
            documents.append(
                {"key": key, "label": label, "url": url, "required": bool(item.get("required"))}
            )
        out["documents"] = documents
    for name, limit in (("version", 40), ("notice", 300)):
        if name in source and source[name] not in (None, ""):
            value = source[name]
            if isinstance(value, str) and len(value) <= limit:
                out[name] = value
            else:
                problems.append(f"{name} must be text up to {limit} characters")
    if strict and problems:
        raise ValueError("; ".join(problems))
    return out


def documents(legal: Any, branding: Any = None) -> list[dict[str, Any]]:
    """Every document, with the branding's older terms and privacy links standing in when none are set."""
    listed = clean(legal).get("documents") or []
    if listed:
        return listed
    legacy = branding if isinstance(branding, dict) else {}
    return [
        {"key": key, "label": PRESETS[key], "url": legacy[f"{key}_url"], "required": True}
        for key in ("terms", "privacy")
        if legacy.get(f"{key}_url")
    ]


def problem(docs: list[dict[str, Any]], accepted: bool) -> str | None:
    if any(d["required"] for d in docs) and not accepted:
        return "You need to accept the terms to create an account"
    return None


def consent(legal: Any, docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What a sign-up accepted, for the person's record. None when nothing was required."""
    required = [d for d in docs if d["required"]]
    if not required:
        return None
    return {
        "version": clean(legal).get("version", ""),
        "accepted_at": datetime.now(UTC).isoformat(),
        "documents": [{"key": d["key"], "url": d["url"]} for d in required],
    }
