"""What an environment asks for when someone signs up, and how they may sign in.

An environment owner picks, per attribute, whether it is asked (off, optional or required), adds their own questions, and chooses which
identifiers (email, username, phone) may be used to sign in. The same definition drives the hosted sign-up page, the sign-up API and
Studio's editor, so a required answer is required everywhere. Answers land on the person: ``name``, ``username`` and ``phone`` are
columns, everything else is ``user_metadata``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit

MODES = ("off", "optional", "required")
IDENTIFIERS = ("email", "username", "phone")
KINDS = ("text", "textarea", "number", "date", "tel", "url", "select", "checkbox")
SLUG = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PHONE = re.compile(r"^\+?[0-9][0-9 ()\-.]{5,22}$")
USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,39}$")
MAX_CUSTOM = 12

COUNTRIES = [
    "Algeria", "Argentina", "Australia", "Austria", "Bangladesh", "Belgium", "Botswana", "Brazil", "Cameroon", "Canada",
    "Chile", "China", "Colombia", "Côte d'Ivoire", "Denmark", "Egypt", "Ethiopia", "Finland", "France", "Germany", "Ghana",
    "Greece", "India", "Indonesia", "Ireland", "Israel", "Italy", "Japan", "Kenya", "Malaysia", "Mexico", "Morocco",
    "Netherlands", "New Zealand", "Nigeria", "Norway", "Pakistan", "Philippines", "Poland", "Portugal", "Rwanda",
    "Saudi Arabia", "Senegal", "Singapore", "South Africa", "South Korea", "Spain", "Sweden", "Switzerland", "Tanzania",
    "Thailand", "Turkey", "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom", "United States", "Vietnam",
    "Zambia", "Zimbabwe", "Other",
]  # fmt: skip

#: Attributes a person may be asked for. ``store`` says where the answer lives on the person.
STANDARD: dict[str, dict[str, Any]] = {
    "given_name": {"label": "First name", "type": "text", "store": "meta", "auto": "given-name", "group": "identity"},
    "family_name": {"label": "Last name", "type": "text", "store": "meta", "auto": "family-name", "group": "identity"},
    "name": {"label": "Full name", "type": "text", "store": "name", "auto": "name", "group": "identity"},
    "username": {"label": "Username", "type": "text", "store": "username", "auto": "username", "group": "account"},
    "phone": {"label": "Phone number", "type": "tel", "store": "phone", "auto": "tel", "group": "profile"},
    "birthdate": {"label": "Date of birth", "type": "date", "store": "meta", "auto": "bday", "group": "profile"},
    "gender": {
        "label": "Gender", "type": "select", "store": "meta", "group": "profile",
        "options": ["Female", "Male", "Non-binary", "Prefer not to say"],
    },
    "company": {"label": "Company", "type": "text", "store": "meta", "auto": "organization", "group": "profile"},
    "job_title": {"label": "Job title", "type": "text", "store": "meta", "auto": "organization-title", "group": "profile"},
    "website": {"label": "Website", "type": "url", "store": "meta", "auto": "url", "group": "profile"},
    "country": {"label": "Country", "type": "select", "store": "meta", "group": "profile", "options": COUNTRIES},
    "address": {"label": "Address", "type": "textarea", "store": "meta", "auto": "street-address", "group": "profile"},
}  # fmt: skip

DEFAULT_FORM: dict[str, Any] = {
    "sign_in_with": ["email"],
    "attributes": {},
    "custom": [],
    "locked": [],
    "claims": [],
    "steps": "auto",
}


def _safe_url(value: str) -> bool:
    parts = urlsplit(value)
    if parts.scheme == "https" and parts.netloc:
        return True
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")


def clean_custom(raw: Any, problems: list[str]) -> list[dict[str, Any]]:
    """Your own questions: ``[{key, label, type, required, placeholder, help, options, min, max}]``."""
    if not isinstance(raw, list):
        problems.append("custom must be a list")
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw[:MAX_CUSTOM]:
        if not isinstance(item, dict):
            problems.append("each question must be an object")
            continue
        key, label, kind = (
            item.get("key"),
            str(item.get("label") or "").strip(),
            item.get("type", "text"),
        )
        if (
            not isinstance(key, str)
            or not SLUG.match(key)
            or key in seen
            or key in ("email", "password", "accepted_terms")
        ):
            problems.append(
                f"question key {key!r} must be unique lowercase letters, digits and underscores"
            )
            continue
        if not label or len(label) > 80:
            problems.append(f"question {key} needs a label of up to 80 characters")
            continue
        if kind not in KINDS:
            problems.append(f"question {key}: type must be one of {', '.join(KINDS)}")
            continue
        entry: dict[str, Any] = {
            "key": key,
            "label": label,
            "type": kind,
            "required": bool(item.get("required")),
        }
        if item.get("ask") is False:
            entry["ask"] = False  # kept on the person's profile, not asked at sign-up
        for extra in ("placeholder", "help"):
            if item.get(extra):
                entry[extra] = str(item[extra])[:160]
        if kind == "select":
            options = [str(o)[:80] for o in (item.get("options") or []) if str(o).strip()][:30]
            if not options:
                problems.append(f"question {key} needs at least one choice")
                continue
            entry["options"] = options
        if kind in ("text", "textarea", "number"):
            for bound in ("min", "max"):
                value = item.get(bound)
                if value in (None, ""):
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    problems.append(f"question {key}: {bound} must be a number")
                    continue
                entry[bound] = int(number) if number == int(number) else number
        seen.add(key)
        out.append(entry)
    return out


def clean(raw: Any, *, strict: bool = False) -> dict[str, Any]:
    """The sign-up form that may be stored. ``strict`` raises ``ValueError`` listing every problem; otherwise bad values are dropped."""
    source = raw if isinstance(raw, dict) else {}
    problems: list[str] = []
    out: dict[str, Any] = {}
    if "sign_in_with" in source:
        value = source["sign_in_with"]
        if (
            isinstance(value, list)
            and value
            and all(v in IDENTIFIERS for v in value)
            and "email" in value
        ):
            out["sign_in_with"] = [i for i in IDENTIFIERS if i in value]
        else:
            problems.append("sign_in_with must include email and may add username and phone")
    if "attributes" in source:
        attributes = source["attributes"]
        if isinstance(attributes, dict):
            cleaned = {}
            for key, mode in attributes.items():
                if key not in STANDARD:
                    problems.append(f"{key!r} is not a standard attribute")
                elif mode not in MODES:
                    problems.append(f"{key} must be off, optional or required")
                elif mode != "off":
                    cleaned[key] = mode
            out["attributes"] = cleaned
        else:
            problems.append("attributes must be an object")
    if "custom" in source:
        out["custom"] = clean_custom(source["custom"], problems)
    for listed in ("locked", "claims"):
        if listed in source:
            value = source[listed]
            if isinstance(value, list) and all(isinstance(k, str) and len(k) <= 64 for k in value):
                out[listed] = sorted(set(value))[:100]
            else:
                problems.append(f"{listed} must be a list of attribute keys")
    if "steps" in source:
        if source["steps"] in ("auto", "single", "two"):
            out["steps"] = source["steps"]
        else:
            problems.append("steps must be auto, single or two")
    # An identifier people sign in with has to be asked for, and has to be there.
    for identifier, key in (("username", "username"), ("phone", "phone")):
        if identifier in out.get("sign_in_with", []):
            out.setdefault("attributes", {})[key] = "required"
    if strict and problems:
        raise ValueError("; ".join(problems))
    return out


def resolved(raw: Any) -> dict[str, Any]:
    return {**DEFAULT_FORM, **clean(raw)}


def sign_in_with(raw: Any) -> list[str]:
    return resolved(raw)["sign_in_with"]


def fields(raw: Any, legacy: Any = None, *, hidden: bool = False) -> list[dict[str, Any]]:
    """Every question sign-up asks, in page order, each ``{key, label, type, required, store, group, …}``.

    ``legacy`` is the older list of questions that lived with the branding; it is read as part of this form.
    """
    form = resolved(raw)
    attributes = dict(form["attributes"])
    custom: list[dict[str, Any]] = [dict(c) for c in form["custom"]]
    taken = {c["key"] for c in custom}
    problems: list[str] = []
    for item in clean_custom(legacy or [], problems):
        if item["key"] in STANDARD and item["key"] not in attributes:
            attributes[item["key"]] = "required" if item["required"] else "optional"
        elif item["key"] not in taken and item["key"] not in STANDARD:
            custom.append(item)
            taken.add(item["key"])
    if not attributes and not custom:
        attributes = {"name": "optional"}
    out: list[dict[str, Any]] = []
    for key, spec in STANDARD.items():
        mode = attributes.get(key)
        if mode in ("optional", "required"):
            entry = {"key": key, "required": mode == "required", **spec}
            if key == "username":
                entry["help"] = "Letters, numbers, dots, dashes and underscores."
            out.append(entry)
    for item in custom:
        if hidden or item.get("ask", True):
            out.append({**item, "store": "meta", "group": "profile"})
    return out


@dataclass
class Collected:
    """The answers of one sign-up, checked and sorted to where they are kept."""

    name: str | None = None
    username: str | None = None
    phone: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def normalise_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return ("+" if value.strip().startswith("+") else "") + digits


def collect(
    raw: Any, answers: dict[str, Any], legacy: Any = None, *, partial: bool = False
) -> Collected:
    """Check ``answers`` against the form. Keys the form does not ask for are ignored, never stored.

    ``partial`` is for an update to a profile that already exists: only the keys present are checked, and nothing is *required*.
    """
    result = Collected()
    for item in fields(raw, legacy, hidden=partial):
        key, kind, label = item["key"], item["type"], item["label"]
        if partial and key not in answers:
            continue
        item = {**item, "required": item["required"] and not partial}
        value: Any = answers.get(key)
        if kind == "checkbox":
            value = value is True or value in ("true", "on", "1", 1)
            if item["required"] and not value:
                result.problems.append(f"{label} is required")
        else:
            value = str(value).strip() if value not in (None, "") else ""
            if not value:
                if item["required"]:
                    result.problems.append(f"{label} is required")
                continue
            limit = 1000 if kind == "textarea" else 200
            if len(value) > limit:
                result.problems.append(f"{label} is too long")
                continue
            if kind in ("text", "textarea") and "min" in item and len(value) < item["min"]:
                result.problems.append(f"{label} must be at least {item['min']} characters")
            if kind in ("text", "textarea") and "max" in item and len(value) > item["max"]:
                result.problems.append(f"{label} must be at most {item['max']} characters")
            if kind == "tel":
                if not PHONE.match(value):
                    result.problems.append(f"{label} must be a phone number")
                    continue
                value = normalise_phone(value)
            elif kind == "number":
                try:
                    number = float(value)
                except ValueError:
                    result.problems.append(f"{label} must be a number")
                    continue
                if "min" in item and number < item["min"]:
                    result.problems.append(f"{label} must be at least {item['min']}")
                if "max" in item and number > item["max"]:
                    result.problems.append(f"{label} must be at most {item['max']}")
                value = int(number) if number == int(number) else number
            elif kind == "date":
                try:
                    when = date.fromisoformat(value)
                except ValueError:
                    result.problems.append(f"{label} must be a date")
                    continue
                if key == "birthdate" and not (date(1900, 1, 1) <= when <= date.today()):
                    result.problems.append(f"{label} is not a valid date")
                    continue
            elif kind == "url":
                if not _safe_url(value):
                    result.problems.append(f"{label} must be a web address starting with https://")
                    continue
            elif kind == "select" and value not in item["options"]:
                result.problems.append(f"{label} must be one of the choices")
                continue
            if key == "username":
                value = value.lower()
                if not USERNAME.match(value):
                    result.problems.append(
                        "Username must be 3 to 40 letters, numbers, dots, dashes or underscores"
                    )
                    continue
        if item["store"] == "name":
            result.name = value or None
        elif item["store"] == "username":
            result.username = value or None
        elif item["store"] == "phone":
            result.phone = value or None
        elif value not in ("", None) and (kind != "checkbox" or value):
            result.metadata[key] = value
    if not result.name:
        joined = " ".join(
            str(result.metadata.get(k, ""))
            for k in ("given_name", "family_name")
            if result.metadata.get(k)
        ).strip()
        result.name = joined or None
    return result
