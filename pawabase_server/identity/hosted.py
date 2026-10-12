"""The hosted sign-in pages: what they look like (branding), what sign-up asks for, and the HTML that is served.

Branding is a small, closed set of design tokens, wording and form fields, never markup. Every value is validated here and escaped when
rendered, so an environment's owner cannot put script or a look-alike form on a page served from the gateway's address.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlsplit

from pawabase_server.identity import signup_form

COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

FONTS = {
    "system": 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    "rounded": 'ui-rounded, "SF Pro Rounded", "Nunito", system-ui, sans-serif',
    "serif": 'ui-serif, Georgia, "Times New Roman", serif',
    "mono": 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
}

FIELD_TYPES = ("text", "tel", "textarea", "select", "checkbox")

DEFAULTS: dict[str, Any] = {
    "name": "",
    "logo_url": "",
    "primary": "#6d5bd0",
    "background": "#f3f1fb",
    "card": "#ffffff",
    "text": "#1f1b2e",
    "radius": 14,
    "font": "system",
    "theme": "light",
    "layout": "split",
    "tagline": "",
    "hero_url": "",
    "login_title": "Welcome back",
    "login_subtitle": "",
    "signup_title": "Create your account",
    "signup_subtitle": "",
    "terms_url": "",
    "privacy_url": "",
    "footer": "",
    "signup_fields": [],
}

LIMITS = {
    "name": 80,
    "tagline": 140,
    "login_title": 80,
    "login_subtitle": 200,
    "signup_title": 80,
    "signup_subtitle": 200,
    "footer": 200,
    "logo_url": 2048,
    "hero_url": 2048,
    "terms_url": 2048,
    "privacy_url": 2048,
}
URLS = ("logo_url", "hero_url", "terms_url", "privacy_url")
COLORS = ("primary", "background", "card", "text")
MAX_FIELDS = 8


def _safe_url(value: str) -> bool:
    parts = urlsplit(value)
    if parts.scheme == "https" and parts.netloc:
        return True
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")


def clean_fields(raw: Any, problems: list[str]) -> list[dict[str, Any]]:
    """The extra questions sign-up asks: ``[{key, label, type, required, placeholder, help, options}]``."""
    if not isinstance(raw, list):
        problems.append("signup_fields must be a list")
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw[:MAX_FIELDS]:
        if not isinstance(item, dict):
            problems.append("each sign-up field must be an object")
            continue
        key, label = item.get("key"), str(item.get("label") or "").strip()
        kind = item.get("type", "text")
        if (
            not isinstance(key, str)
            or not SLUG.match(key)
            or key in seen
            or key in ("email", "password")
        ):
            problems.append(
                f"sign-up field key {key!r} must be unique lowercase letters, digits and underscores"
            )
            continue
        if not label or len(label) > 80:
            problems.append(f"sign-up field {key} needs a label of up to 80 characters")
            continue
        if kind not in FIELD_TYPES:
            problems.append(f"sign-up field {key}: type must be one of {', '.join(FIELD_TYPES)}")
            continue
        field: dict[str, Any] = {
            "key": key,
            "label": label,
            "type": kind,
            "required": bool(item.get("required")),
        }
        for extra in ("placeholder", "help"):
            if item.get(extra):
                field[extra] = str(item[extra])[:160]
        if kind == "select":
            options = [str(o)[:80] for o in (item.get("options") or []) if str(o).strip()][:20]
            if not options:
                problems.append(f"sign-up field {key} needs at least one option")
                continue
            field["options"] = options
        seen.add(key)
        out.append(field)
    return out


def clean_branding(raw: Any, *, strict: bool = False) -> dict[str, Any]:
    """The branding that may be stored or rendered. ``strict`` raises ``ValueError`` listing every problem; otherwise bad values are dropped."""
    source = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    problems: list[str] = []
    for key in DEFAULTS:
        if key not in source or source[key] in (None, ""):
            continue
        value = source[key]
        if key in COLORS:
            if isinstance(value, str) and COLOR.match(value):
                out[key] = value.lower()
            else:
                problems.append(f"{key} must be a colour like #6d5bd0")
        elif key == "radius":
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 28:
                out[key] = int(value)
            else:
                problems.append("radius must be a number from 0 to 28")
        elif key == "font":
            if value in FONTS:
                out[key] = value
            else:
                problems.append(f"font must be one of {', '.join(FONTS)}")
        elif key == "theme":
            if value in ("light", "dark", "auto"):
                out[key] = value
            else:
                problems.append("theme must be light, dark or auto")
        elif key == "layout":
            if value in ("split", "center"):
                out[key] = value
            else:
                problems.append("layout must be split or center")
        elif key == "signup_fields":
            out[key] = clean_fields(value, problems)
        elif key in URLS:
            if isinstance(value, str) and len(value) <= LIMITS[key] and _safe_url(value):
                out[key] = value
            else:
                problems.append(f"{key} must be an https address")
        elif isinstance(value, str) and len(value) <= LIMITS.get(key, 200):
            out[key] = value
        else:
            problems.append(f"{key} must be text up to {LIMITS.get(key, 200)} characters")
    if strict and problems:
        raise ValueError("; ".join(problems))
    return out


def resolved(raw: Any) -> dict[str, Any]:
    return {**DEFAULTS, **clean_branding(raw)}


def validate_signup(
    branding: Any, fields: dict[str, Any], accepted_terms: bool, form: Any = None
) -> tuple[dict[str, Any], str | None, list[str]]:
    """Check a sign-up's answers against the configured form and terms.

    Returns ``(metadata to store, name if one was asked for, problems)``. Unknown keys are ignored, never stored.
    """
    collected = signup_form.collect(form, fields, resolved(branding)["signup_fields"])
    problems = list(collected.problems)
    if refusal := terms_problem(branding, accepted_terms):
        problems.append(refusal)
    return collected.metadata, collected.name, problems


PAGES = ("login", "signup", "forgot", "reset")

SOCIAL_ICONS = {
    "google": '<svg viewBox="0 0 24 24" width="18" height="18"><path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.5-.2-2.3H12v4.4h6.5a5.6 5.6 0 0 1-2.4 3.7v3h3.9c2.3-2.1 3.5-5.2 3.5-8.8z"/><path fill="#34A853" d="M12 24c3.2 0 6-1.1 7.9-2.9l-3.9-3c-1.1.7-2.5 1.2-4 1.2-3.1 0-5.7-2.1-6.6-4.9H1.4v3.1A12 12 0 0 0 12 24z"/><path fill="#FBBC05" d="M5.4 14.4a7.2 7.2 0 0 1 0-4.8V6.5H1.4a12 12 0 0 0 0 11z"/><path fill="#EA4335" d="M12 4.8c1.8 0 3.3.6 4.6 1.8l3.4-3.4A12 12 0 0 0 1.4 6.5l4 3.1C6.3 6.9 8.9 4.8 12 4.8z"/></svg>',
    "github": '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 .3a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2c-3.3.7-4-1.6-4-1.6-.6-1.4-1.4-1.8-1.4-1.8-1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.7-1.6-2.7-.3-5.5-1.3-5.5-6 0-1.2.5-2.3 1.3-3.1-.2-.4-.6-1.6 0-3.2 0 0 1-.3 3.3 1.2a11.500 11.500 0 0 1 6 0c2.300-1.500 3.300-1.200 3.300-1.200.7 1.600.2 2.800.1 3.200.8.800 1.300 1.900 1.300 3.100 0 4.600-2.800 5.600-5.500 5.900.4.400.8 1.100.8 2.200v3.300c0 .3.2.7.8.6A12 12 0 0 0 12 .3"/></svg>',
    "microsoft": '<svg viewBox="0 0 24 24" width="18" height="18"><path fill="#F25022" d="M1 1h10.5v10.5H1z"/><path fill="#7FBA00" d="M12.5 1H23v10.5H12.5z"/><path fill="#00A4EF" d="M1 12.5h10.5V23H1z"/><path fill="#FFB900" d="M12.5 12.5H23V23H12.5z"/></svg>',
    "discord": '<svg viewBox="0 0 24 24" width="18" height="18" fill="#5865F2"><path d="M20.3 4.4A19.800 19.800 0 0 0 15.400 2.900l-.6 1.200a18.300 18.300 0 0 0-5.500 0L8.700 2.900a19.700 19.700 0 0 0-4.900 1.500C.7 9 0 13.500.3 17.900a19.900 19.900 0 0 0 6 3l1.300-2.100c-.7-.3-1.400-.6-2-1l.5-.4a14.200 14.200 0 0 0 12.200 0l.5.4c-.6.400-1.300.7-2 1l1.300 2.100a19.800 19.800 0 0 0 6-3c.4-5.100-.7-9.600-3.800-13.500zM8 15.200c-1.200 0-2.100-1.100-2.100-2.400s.9-2.400 2.100-2.400 2.100 1.100 2.100 2.400-.9 2.400-2.100 2.400zm8 0c-1.200 0-2.100-1.100-2.100-2.400s.9-2.400 2.100-2.400 2.100 1.100 2.100 2.400-.9 2.400-2.100 2.400z"/></svg>',
}

EYE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.600-7 10-7 10 7 10 7-3.600 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>'


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _input(field: dict[str, Any]) -> str:
    key, label = field["key"], _e(field["label"])
    req = " required" if field["required"] else ""
    mark = "" if field["required"] else '<span class="opt">optional</span>'
    help_ = f"<small>{_e(field['help'])}</small>" if field.get("help") else ""
    ph = f' placeholder="{_e(field["placeholder"])}"' if field.get("placeholder") else ""
    kind = field["type"]
    if kind == "checkbox":
        return f'<div class="fld"><label class="check"><input type="checkbox" name="f_{key}"{req}><span>{label}</span></label>{help_}</div>'
    auto = f' autocomplete="{_e(field["auto"])}"' if field.get("auto") else ""
    if kind == "select":
        options = "".join(f'<option value="{_e(o)}">{_e(o)}</option>' for o in field["options"])
        control = (
            f'<select name="f_{key}"{req}{auto}><option value="">Choose…</option>{options}</select>'
        )
    elif kind == "textarea":
        control = f'<textarea name="f_{key}" rows="3"{req}{ph}{auto}></textarea>'
    else:
        html_type = {"tel": "tel", "number": "number", "date": "date", "url": "url"}.get(
            kind, "text"
        )
        bounds = ""
        if kind == "number":
            bounds = "".join(f' {b}="{_e(field[b])}"' for b in ("min", "max") if b in field)
        elif kind == "text":
            bounds = "".join(
                f' {a}length="{_e(int(field[b]))}"'
                for a, b in (("min", "min"), ("max", "max"))
                if b in field
            )
        extra = ' autocapitalize="none" spellcheck="false"' if key == "username" else ""
        control = f'<input type="{html_type}" name="f_{key}"{req}{ph}{auto}{bounds}{extra}>'
    return f'<label class="fld"><span class="lab">{label}{mark}</span>{control}{help_}</label>'


def _password(
    name: str, label: str, autocomplete: str, *, rules: list[tuple[str, str]] | None
) -> str:
    checklist = ""
    if rules is not None:
        items = "".join(f'<li data-rule="{rid}">{_e(text)}</li>' for rid, text in rules)
        checklist = (
            f'<div class="meter" aria-hidden="true"><i></i></div><ul class="rules">{items}</ul>'
        )
    return (
        f'<div class="fld"><label class="lab" for="{name}">{_e(label)}</label><span class="pw"><input id="{name}" name="{name}" type="password" '
        f'autocomplete="{autocomplete}" required><button type="button" class="eye" '
        f'aria-label="Show password" data-eye>{EYE}</button></span>{checklist}</div>'
    )


def _legal_checkbox(b: dict[str, Any]) -> str:
    if not (b["terms_url"] or b["privacy_url"]):
        return ""
    parts = []
    if b["terms_url"]:
        parts.append(
            f'<a href="{_e(b["terms_url"])}" target="_blank" rel="noopener noreferrer">Terms of Service</a>'
        )
    if b["privacy_url"]:
        parts.append(
            f'<a href="{_e(b["privacy_url"])}" target="_blank" rel="noopener noreferrer">Privacy Policy</a>'
        )
    return f'<div class="fld"><label class="check"><input type="checkbox" name="accepted_terms" required><span>I agree to the {" and ".join(parts)}</span></label></div>'


def terms_problem(branding: Any, accepted: bool) -> str | None:
    b = resolved(branding)
    if (b["terms_url"] or b["privacy_url"]) and not accepted:
        return "You need to accept the terms to create an account"
    return None


def _fields_html(items: list[dict[str, Any]]) -> str:
    """The questions as HTML, with first and last name side by side."""
    out, index = [], 0
    while index < len(items):
        item = items[index]
        nxt = items[index + 1] if index + 1 < len(items) else None
        if item["key"] == "given_name" and nxt and nxt["key"] == "family_name":
            out.append(f'<div class="pair">{_input(item)}{_input(nxt)}</div>')
            index += 2
            continue
        out.append(_input(item))
        index += 1
    return "".join(out)


def password_rules_list(minimum: int, rules: dict[str, bool] | None) -> list[tuple[str, str]]:
    listed = [("len", f"At least {minimum} characters")]
    for rid, text in (
        ("upper", "An uppercase letter"),
        ("lower", "A lowercase letter"),
        ("digit", "A number"),
        ("symbol", "A symbol"),
    ):
        if (rules or {}).get(rid):
            listed.append((rid, text))
    return listed


def render(
    page: str,
    *,
    env: str,
    project_name: str,
    branding: Any,
    providers: list[str],
    signup_enabled: bool,
    magic_link_enabled: bool,
    query: dict[str, str],
    nonce: str,
    base: str,
    preview: bool = False,
    password_min: int = 8,
    password_rules: dict[str, bool] | None = None,
    form: Any = None,
    notice: tuple[str, str, str, str] | None = None,
) -> str:
    """One hosted page as HTML. ``base`` is the path the page posts to (``/auth/v1/hosted/<env>``).

    ``form`` is the environment's sign-up form (see ``signup_form``). ``notice`` is ``(title, message, button label, button
    address)``, for the pages that only say something.
    """
    b = resolved(branding)
    name = b["name"] or project_name or "Sign in"
    dark, auto = b["theme"] == "dark", b["theme"] == "auto"
    pairs = {k: query.get(k, "") for k in ("redirect_to", "state", "code_challenge", "token")}
    carried = "&".join(f"{k}={_e(v)}" for k, v in pairs.items() if v and k != "token")
    suffix = f"?{carried}" if carried else ""
    identifiers = signup_form.sign_in_with(form)

    title = {
        "login": b["login_title"],
        "signup": b["signup_title"],
        "forgot": "Reset your password",
        "reset": "Choose a new password",
    }.get(page, "")
    subtitle = {
        "login": b["login_subtitle"],
        "signup": b["signup_subtitle"],
        "forgot": "Enter your email address and we'll send you a link to choose a new password.",
        "reset": "Pick something you haven't used before.",
    }.get(page, "")
    if notice:
        title, subtitle = notice[0], notice[1]

    logo = (
        f'<img class="logo" src="{_e(b["logo_url"])}" alt="{_e(name)}">'
        if b["logo_url"]
        else f'<div class="wordmark">{_e(name)}</div>'
    )
    social = "".join(
        f'<a class="social" href="/auth/v1/authorize/{_e(env)}/{_e(p)}?{carried}">{SOCIAL_ICONS.get(p, "")}<span>Continue with {_e(p.title())}</span></a>'
        for p in providers
    )
    divider = '<div class="or"><span>or</span></div>' if social else ""

    extra = ""
    body = ""
    links = ""
    steps = 1
    if notice:
        links = (
            f'<a class="btn-link" href="{_e(notice[3])}">{_e(notice[2])}</a>' if notice[3] else ""
        )
    elif page == "login":
        label = {
            ("email",): "Email",
            ("email", "username"): "Email or username",
            ("email", "phone"): "Email or phone number",
        }.get(tuple(identifiers), "Email, username or phone")
        kind = "email" if identifiers == ["email"] else "text"
        body = (
            f'<div class="fld"><label class="lab" for="email">{label}</label><input id="email" name="email" type="{kind}" autocomplete="username" required autofocus autocapitalize="none" spellcheck="false"></div>'
            + _password("password", "Password", "current-password", rules=None)
            + '<div class="row"><label class="check quiet"><input type="checkbox" name="remember" checked><span>Remember me</span></label>'
            + f'<a href="{base}/forgot{suffix}">Forgot password?</a></div>'
            + '<button class="primary" type="submit"><span>Sign in</span></button>'
        )
        links = (
            f'<span>New here? <a href="{base}/signup{suffix}">Create an account</a></span>'
            if signup_enabled
            else ""
        )
        extra = (
            '<button class="ghost" type="button" id="magic">Email me a sign-in link instead</button>'
            if magic_link_enabled
            else ""
        )
    elif page == "signup":
        asked = signup_form.fields(form, b["signup_fields"])
        account = [f for f in asked if f["group"] == "account"]
        rest = [f for f in asked if f["group"] != "account"]
        mode = signup_form.resolved(form)["steps"]
        two = mode == "two" or (mode == "auto" and len(rest) >= 3)
        email = '<div class="fld"><label class="lab" for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required autocapitalize="none" spellcheck="false"></div>'
        pw = _password(
            "password",
            "Password",
            "new-password",
            rules=password_rules_list(password_min, password_rules),
        )
        terms = _legal_checkbox(b)
        submit = '<button class="primary" type="submit"><span>Create account</span></button>'
        if two and rest:
            steps = 2
            body = (
                '<div class="step" data-step="1">'
                + _fields_html(account)
                + email
                + pw
                + '<button class="primary" type="button" id="next"><span>Continue</span></button></div>'
                + '<div class="step" data-step="2" hidden>'
                + _fields_html(rest)
                + terms
                + submit
                + '<button class="ghost" type="button" id="back">Back</button></div>'
            )
        else:
            names = [f for f in rest if f["group"] == "identity"]
            profile = [f for f in rest if f["group"] != "identity"]
            body = (
                _fields_html(names)
                + _fields_html(account)
                + email
                + pw
                + _fields_html(profile)
                + terms
                + submit
            )
        links = f'<span>Already have an account? <a href="{base}/login{suffix}">Sign in</a></span>'
    elif page == "forgot":
        body = (
            '<div class="fld"><label class="lab" for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required autofocus></div>'
            '<button class="primary" type="submit"><span>Send the link</span></button>'
        )
        links = f'<a href="{base}/login{suffix}">Back to sign in</a>'
    else:  # reset
        body = (
            _password(
                "password",
                "New password",
                "new-password",
                rules=password_rules_list(password_min, password_rules),
            )
            + '<button class="primary" type="submit"><span>Save the new password</span></button>'
        )
        links = f'<a href="{base}/login">Back to sign in</a>'

    legal_parts = [
        f'<a href="{_e(b[key])}" target="_blank" rel="noopener noreferrer">{label}</a>'
        for key, label in (("terms_url", "Terms"), ("privacy_url", "Privacy"))
        if b[key]
    ]
    legal = " · ".join(legal_parts + ([_e(b["footer"])] if b["footer"] else []))

    dark_vars = (
        "--bg:#14121f;--card:#1d1a2b;--text:#f3f1fa;--muted:#a8a3bd;--line:#34304a;--soft:#262338;"
    )
    light_vars = (
        f"--bg:{b['background']};--card:{b['card']};--text:{b['text']};--muted:color-mix(in srgb,{b['text']} 62%,{b['card']});"
        f"--line:color-mix(in srgb,{b['text']} 16%,{b['card']});--soft:color-mix(in srgb,{b['text']} 5%,{b['card']});"
    )
    root = (
        dark_vars if dark else light_vars
    ) + f"--primary:{b['primary']};--radius:{b['radius']}px;--font:{FONTS[b['font']]};"
    media = f"@media (prefers-color-scheme: dark){{:root{{{dark_vars}}}}}" if auto else ""
    split = b["layout"] == "split" and not notice
    hero = (
        f"background-image:linear-gradient(160deg,color-mix(in srgb,var(--primary) 82%,#000),color-mix(in srgb,var(--primary) 55%,transparent)),url('{_e(b['hero_url'])}');background-size:cover;background-position:center;"
        if b["hero_url"]
        else ""
    )
    tagline = _e(b["tagline"]) if b["tagline"] else f"Sign in to {_e(name)}"
    panel = (
        f'<aside class="brand" style="{hero}"><div class="brand-in"><div class="brand-name">{_e(name)}</div><p>{tagline}</p></div></aside>'
        if split
        else ""
    )
    columns = "minmax(380px,5fr) minmax(0,6fr)" if split else "1fr"
    pane_bg = "var(--card)" if split else "transparent"
    card_style = (
        ""
        if split
        else "background:var(--card);border:1px solid var(--line);border-radius:calc(var(--radius) + 10px);padding:48px 44px 40px;box-shadow:0 24px 60px rgba(20,20,40,.08)"
    )
    preview_css = (
        ".preview{position:fixed;top:10px;right:12px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);z-index:5}"
        if preview
        else ""
    )
    badge = '<div class="preview">Preview</div>' if preview else ""
    sub = f'<p class="sub">{_e(subtitle)}</p>' if subtitle else ""
    progress = (
        '<div class="progress" aria-hidden="true"><i class="on"></i><i></i></div>'
        if steps == 2
        else ""
    )
    with_social = page in ("login", "signup") and not notice
    post_path = {"login": "signin", "signup": "signup", "forgot": "forgot", "reset": "reset"}.get(
        page, ""
    )
    env_key = json.dumps(f"pawabase.email.{env}")
    script = f"""
const base = {json.dumps(base)};
const carried = {json.dumps(pairs)};
const preview = {json.dumps(preview)};
const page = {json.dumps(page)};
const form = document.getElementById('form');
const note = document.getElementById('note');
let mfa = null;
function say(text, bad) {{ if (!note) return; note.textContent = text; note.className = 'note ' + (bad ? 'bad' : 'ok'); }}
async function post(path, body) {{
  const r = await fetch(base + path, {{ method: 'POST', headers: {{ 'content-type': 'application/json' }}, body: JSON.stringify({{ ...carried, ...body }}) }});
  const j = await r.json().catch(() => ({{}}));
  if (!r.ok) {{ const d = j.detail; throw new Error(typeof d === 'string' ? d : (d && (d.message && d.problems ? d.problems.join(' ') : d.message || (d.problems || []).join(' '))) || 'Something went wrong. Try again.'); }}
  return j;
}}
function finish(title, text) {{ if (form) form.remove(); const m = document.getElementById('magic'); if (m) m.remove(); const pr = document.querySelector('.progress'); if (pr) pr.remove(); const h = document.querySelector('h1'); if (h && title) h.textContent = title; const s = document.querySelector('p.sub'); if (s) s.remove(); say(text); }}
function done(j) {{
  if (j.mfa_required) {{ mfa = j.mfa_token; form.innerHTML = '<div class="fld"><label class="lab" for="code">Verification code</label><input id="code" name="code" class="code" inputmode="numeric" autocomplete="one-time-code" placeholder="123456" required autofocus></div><button class="primary" type="submit"><span>Verify</span></button>'; say('Enter the 6-digit code from your authenticator app, or a recovery code.'); return; }}
  if (j.redirect) {{ say('Signed in. Taking you back…'); location.href = j.redirect; return; }}
  if (j.verify) return finish('Check your email', 'We sent a link to confirm your address. Open it, then sign in.');
  if (j.sent) return finish('Check your email', j.message || 'If the address has an account, a message is on its way.');
  if (j.changed) return finish('Password updated', 'Your password was changed. You can sign in now.');
  if (j.signed_in) return finish('You are signed in', 'You can close this window.');
}}
const TESTS = {{ len: (v) => v.length >= {password_min}, upper: (v) => /[A-Z]/.test(v), lower: (v) => /[a-z]/.test(v), digit: (v) => /\\d/.test(v), symbol: (v) => /[^A-Za-z0-9]/.test(v) }};
function strength(v) {{
  let s = 0; if (v.length >= 8) s++; if (v.length >= 12) s++; if (/[a-z]/.test(v) && /[A-Z]/.test(v)) s++; if (/\\d/.test(v)) s++; if (/[^A-Za-z0-9]/.test(v)) s++;
  return Math.min(4, s);
}}
document.querySelectorAll('[data-eye]').forEach((b) => b.addEventListener('click', () => {{
  const i = b.parentElement.querySelector('input'); const show = i.type === 'password'; i.type = show ? 'text' : 'password'; b.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
}}));
const meter = document.querySelector('.meter i');
if (meter) form.querySelector('input[name=password]').addEventListener('input', (e) => {{
  const v = e.target.value; const n = strength(v); meter.style.width = (v ? Math.max(1, n) * 25 : 0) + '%'; meter.dataset.level = n;
  document.querySelectorAll('.rules li').forEach((li) => li.classList.toggle('met', !!v && TESTS[li.dataset.rule](v)));
}});
const remembered = (() => {{ try {{ return localStorage.getItem({env_key}); }} catch (e) {{ return null; }} }})();
if (page === 'login' && remembered && form) {{ const f = form.querySelector('input[name=email]'); if (f && !f.value) {{ f.value = remembered; const p = form.querySelector('input[name=password]'); if (p) p.focus(); }} }}
const steps = form ? form.querySelectorAll('.step') : [];
function show(n) {{
  steps.forEach((s) => {{ s.hidden = s.dataset.step !== String(n); }});
  const bars = document.querySelectorAll('.progress i'); bars.forEach((b, i) => b.classList.toggle('on', i < n));
  say('');
  const first = form.querySelector('.step[data-step="' + n + '"] input, .step[data-step="' + n + '"] select'); if (first) first.focus();
}}
function advance() {{
  const here = form.querySelector('.step[data-step="1"]');
  for (const el of here.querySelectorAll('input,select,textarea')) {{ if (!el.reportValidity()) return; }}
  show(2);
}}
const nextButton = document.getElementById('next'); if (nextButton) nextButton.addEventListener('click', advance);
const backButton = document.getElementById('back'); if (backButton) backButton.addEventListener('click', () => show(1));
if (form) form.addEventListener('submit', async (event) => {{
  event.preventDefault();
  if (steps.length && !steps[0].hidden) {{ advance(); return; }}
  if (!form.reportValidity()) return;
  if (preview) {{ say('This is a preview. Nothing is sent.'); return; }}
  const fd = new FormData(form);
  const data = {{}}; const fields = {{}};
  for (const [k, v] of fd.entries()) {{ if (k.startsWith('f_')) fields[k.slice(2)] = v; else data[k] = v; }}
  form.querySelectorAll('input[type=checkbox][name^=f_]').forEach((c) => {{ fields[c.name.slice(2)] = c.checked; }});
  data.accepted_terms = !!form.querySelector('input[name=accepted_terms]:checked');
  if (Object.keys(fields).length) data.fields = fields;
  const button = form.querySelector('button.primary[type=submit]'); button.disabled = true; button.classList.add('busy');
  say('');
  try {{
    if (mfa) done(await post('/signin', {{ mfa_token: mfa, code: data.code }}));
    else {{
      if (page === 'login') {{ try {{ if (data.remember) localStorage.setItem({env_key}, data.email); else localStorage.removeItem({env_key}); }} catch (e) {{}} }}
      done(await post('/{post_path}', data));
    }}
  }} catch (error) {{ say(error.message, true); }}
  button.disabled = false; button.classList.remove('busy');
}});
const magic = document.getElementById('magic');
if (magic) magic.addEventListener('click', async () => {{
  const email = form.querySelector('input[name=email]').value;
  if (!email || !email.includes('@')) {{ say('Enter your email address first.', true); return; }}
  if (preview) {{ say('This is a preview. Nothing is sent.'); return; }}
  try {{ done(await post('/magic', {{ email }})); }} catch (error) {{ say(error.message, true); }}
}});
"""
    form_html = f'<form id="form" novalidate>{body}</form>' if body else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} · {_e(name)}</title>
<style>
:root{{{root}}}{media}
*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%}}
body{{font-family:var(--font);font-size:16px;line-height:1.5;background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased;min-height:100vh}}
[hidden]{{display:none!important}}
.wrap{{min-height:100vh;display:grid;grid-template-columns:{columns}}}
.pane{{display:flex;justify-content:center;align-items:center;padding:64px 32px;background:{pane_bg}}}
main{{width:100%;max-width:420px}}
.card{{{card_style}}}
.brand{{background:linear-gradient(160deg,color-mix(in srgb,var(--primary) 70%,#000),var(--primary) 70%,color-mix(in srgb,var(--primary) 60%,#fff));color:#fff;display:flex;align-items:flex-end;padding:64px;min-height:100vh;position:relative;overflow:hidden}}
.brand::after{{content:"";position:absolute;inset:auto -20% -30% auto;width:70%;aspect-ratio:1;border-radius:50%;background:rgba(255,255,255,.12)}}
.brand-in{{position:relative;z-index:1;max-width:440px}}.brand-name{{font-size:14px;letter-spacing:.14em;text-transform:uppercase;opacity:.8;margin-bottom:16px}}
.brand p{{font-size:34px;line-height:1.18;letter-spacing:-.025em;font-weight:650;margin:0}}
.logo{{display:block;max-height:44px;max-width:180px;margin:0 0 44px}}.wordmark{{font-weight:700;font-size:21px;letter-spacing:-.02em;margin:0 0 44px}}
h1{{font-size:30px;line-height:1.15;letter-spacing:-.025em;font-weight:700;margin:0 0 32px}}h1.tight{{margin-bottom:10px}}p.sub{{margin:0 0 32px;color:var(--muted);font-size:15.5px;line-height:1.55}}h1:last-child{{margin-bottom:0}}
.progress{{display:flex;gap:8px;margin:0 0 28px}}.progress i{{flex:1;height:4px;border-radius:4px;background:var(--line);transition:background .2s}}.progress i.on{{background:var(--primary)}}
form{{display:flex;flex-direction:column;gap:22px}}.step{{display:flex;flex-direction:column;gap:22px}}
.fld{{display:block;margin:0}}.lab{{display:flex;justify-content:space-between;font-size:14px;font-weight:600;margin-bottom:8px}}.opt{{font-weight:500;color:var(--muted);font-size:13px}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
small{{display:block;color:var(--muted);font-size:13px;margin-top:8px;line-height:1.45}}
input:not([type=checkbox]),select,textarea{{display:block;width:100%;min-height:50px;padding:13px 16px;font:inherit;font-size:16px;color:var(--text);background:var(--card);border:1px solid var(--line);border-radius:var(--radius);transition:border-color .15s,box-shadow .15s}}
textarea{{resize:vertical;min-height:96px}}
input:focus,select:focus,textarea:focus{{outline:0;border-color:var(--primary);box-shadow:0 0 0 4px color-mix(in srgb,var(--primary) 20%,transparent)}}
input.code{{letter-spacing:.4em;font-size:22px;text-align:center}}
.pw{{position:relative;display:block}}.pw input{{padding-right:50px}}.eye{{position:absolute;right:8px;top:50%;transform:translateY(-50%);width:36px;height:36px;border:0;background:none;color:var(--muted);cursor:pointer;border-radius:8px;display:grid;place-items:center}}.eye:hover{{background:var(--soft);color:var(--text)}}
.meter{{height:4px;border-radius:4px;background:var(--soft);overflow:hidden;margin:14px 0 0}}.meter i{{display:block;height:100%;width:0;background:#d4483b;transition:width .25s,background .25s}}
.meter i[data-level="2"]{{background:#e0a21b}}.meter i[data-level="3"]{{background:#79a82b}}.meter i[data-level="4"]{{background:#2e9b5b}}
.rules{{list-style:none;margin:14px 0 0;padding:0;display:grid;grid-template-columns:1fr 1fr;gap:8px 16px;font-size:13px;color:var(--muted)}}
.rules li{{display:flex;align-items:center;gap:8px}}.rules li::before{{content:"";width:14px;height:14px;flex:none;border-radius:50%;border:1.5px solid var(--line);background:transparent;transition:all .15s}}
.rules li.met{{color:var(--text)}}.rules li.met::before{{border-color:#2e9b5b;background:#2e9b5b url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 14 14'%3E%3Cpath d='M3.500 7.200l2.300 2.300 4.700-4.800' fill='none' stroke='white' stroke-width='1.800' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center/100% no-repeat}}
.check{{display:flex;gap:12px;align-items:flex-start;font-size:14.5px;line-height:1.5;color:var(--text)}}.check input{{width:18px;height:18px;margin:2px 0 0;accent-color:var(--primary);flex:none}}.check.quiet{{color:var(--muted);font-size:14px;align-items:center}}.check.quiet input{{margin:0}}
.row{{display:flex;justify-content:space-between;align-items:center;gap:12px;font-size:14.5px;margin-top:-6px}}
a{{color:var(--primary);text-decoration:none;font-weight:600}}a:hover{{text-decoration:underline}}
button{{font:inherit;cursor:pointer}}
.primary{{width:100%;min-height:52px;padding:14px;border:0;border-radius:var(--radius);background:var(--primary);color:#fff;font-weight:650;font-size:16px;margin-top:6px;position:relative;transition:filter .15s,transform .05s}}
.primary:hover{{filter:brightness(1.06)}}.primary:active{{transform:translateY(1px)}}.primary:disabled{{cursor:wait;opacity:.85}}
.primary.busy span{{opacity:0}}.primary.busy::after{{content:"";position:absolute;inset:0;margin:auto;width:20px;height:20px;border-radius:50%;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;animation:spin .7s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.ghost{{width:100%;margin-top:4px;background:none;border:0;color:var(--muted);font-size:14.5px;padding:10px}}.ghost:hover{{color:var(--text)}}
.btn-link{{display:inline-block;padding:14px 22px;border-radius:var(--radius);background:var(--primary);color:#fff;text-decoration:none}}
.social{{display:flex;align-items:center;justify-content:center;gap:12px;min-height:50px;padding:12px;margin-bottom:12px;border:1px solid var(--line);border-radius:var(--radius);color:var(--text);font-weight:600;font-size:15px;background:var(--card)}}.social:hover{{background:var(--soft);text-decoration:none}}
.or{{display:flex;align-items:center;gap:16px;color:var(--muted);font-size:13px;margin:28px 0}}.or::before,.or::after{{content:"";flex:1;height:1px;background:var(--line)}}
.links{{margin-top:32px;font-size:14.5px;color:var(--muted);text-align:center}}
.note{{font-size:14.5px;margin:18px 0 0;line-height:1.5}}.note:empty{{display:none}}.note.bad{{color:#c2413b}}.note.ok{{color:var(--muted)}}
.legal{{text-align:center;margin-top:40px;font-size:13px;color:var(--muted)}}.legal a{{color:var(--muted);font-weight:500}}
@media (max-width:860px){{.wrap{{grid-template-columns:1fr}}.brand{{display:none}}.pane{{background:var(--bg);padding:40px 20px;align-items:flex-start}}.card{{background:var(--card);border:1px solid var(--line);border-radius:calc(var(--radius) + 10px);padding:36px 24px 28px}}}}
@media (max-width:420px){{.pair,.rules{{grid-template-columns:1fr}}}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}
{preview_css}
</style></head>
<body>{badge}
<div class="wrap">
  {panel}
  <div class="pane"><main>
    <div class="card">
      {logo}
      {progress}
      <h1{" class=tight" if sub else ""}>{_e(title)}</h1>
      {sub}
      {social if with_social else ""}{divider if with_social else ""}
      {form_html}
      {extra}
      <div id="note" class="note" role="status" aria-live="polite"></div>
      <div class="links">{links}</div>
    </div>
    <div class="legal">{legal}</div>
  </main></div>
</div>
<script nonce="{_e(nonce)}">{script}</script>
</body></html>"""
