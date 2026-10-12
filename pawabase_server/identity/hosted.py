"""The hosted sign-in pages: what they look like (branding) and the HTML that is served.

Branding is a small, closed set of design tokens and wording, never markup. Every value is validated here and escaped when rendered, so
an environment's owner cannot put script or a look-alike form on a page served from the gateway's address.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlsplit

COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

FONTS = {
    "system": 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    "rounded": 'ui-rounded, "SF Pro Rounded", "Nunito", system-ui, sans-serif',
    "serif": 'ui-serif, Georgia, "Times New Roman", serif',
    "mono": 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
}

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
    "login_title": "Welcome back",
    "login_subtitle": "",
    "signup_title": "Create your account",
    "signup_subtitle": "",
    "terms_url": "",
    "privacy_url": "",
    "footer": "",
}

LIMITS = {
    "name": 80,
    "login_title": 80,
    "login_subtitle": 200,
    "signup_title": 80,
    "signup_subtitle": 200,
    "footer": 200,
    "logo_url": 2048,
    "terms_url": 2048,
    "privacy_url": 2048,
}


def _safe_url(value: str) -> bool:
    parts = urlsplit(value)
    if parts.scheme == "https" and parts.netloc:
        return True
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")


def clean_branding(raw: Any, *, strict: bool = False) -> dict[str, Any]:
    """The branding that may be stored or rendered. ``strict`` raises ``ValueError`` listing every problem; otherwise bad values are dropped."""
    source = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    problems: list[str] = []
    for key in DEFAULTS:
        if key not in source or source[key] in (None, ""):
            continue
        value = source[key]
        if key in ("primary", "background", "card", "text"):
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
        elif key in ("logo_url", "terms_url", "privacy_url"):
            if isinstance(value, str) and len(value) <= LIMITS[key] and _safe_url(value):
                out[key] = value
            else:
                problems.append(f"{key} must be an https address")
        else:
            if isinstance(value, str) and len(value) <= LIMITS.get(key, 200):
                out[key] = value
            else:
                problems.append(f"{key} must be text up to {LIMITS.get(key, 200)} characters")
    if strict and problems:
        raise ValueError("; ".join(problems))
    return out


def resolved(raw: Any) -> dict[str, Any]:
    return {**DEFAULTS, **clean_branding(raw)}


PAGES = ("login", "signup", "forgot")


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


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
) -> str:
    """One hosted page as HTML. ``base`` is the path the page posts to (``/auth/v1/hosted/<env>``)."""
    b = resolved(branding)
    name = b["name"] or project_name or "Sign in"
    dark = b["theme"] == "dark"
    auto = b["theme"] == "auto"
    pairs = {
        "redirect_to": query.get("redirect_to", ""),
        "state": query.get("state", ""),
        "code_challenge": query.get("code_challenge", ""),
    }
    carried = "&".join(f"{k}={_e(v)}" for k, v in pairs.items() if v)
    suffix = f"?{carried}" if carried else ""
    title = {
        "login": b["login_title"],
        "signup": b["signup_title"],
        "forgot": "Reset your password",
    }[page]
    subtitle = {
        "login": b["login_subtitle"],
        "signup": b["signup_subtitle"],
        "forgot": "We'll email you a link to choose a new password.",
    }[page]

    logo = (
        f'<img class="logo" src="{_e(b["logo_url"])}" alt="{_e(name)}">'
        if b["logo_url"]
        else f'<div class="wordmark">{_e(name)}</div>'
    )
    social = "".join(
        f'<a class="social" href="/auth/v1/authorize/{_e(env)}/{_e(p)}?{carried}">Continue with {_e(p.title())}</a>'
        for p in providers
    )
    divider = '<div class="or"><span>or</span></div>' if social else ""

    if page == "login":
        fields = (
            '<label>Email<input name="email" type="email" autocomplete="username" required autofocus></label>'
            '<label>Password<input name="password" type="password" autocomplete="current-password" required></label>'
            '<button class="primary" type="submit">Sign in</button>'
        )
        links = f'<a href="{base}/forgot{suffix}">Forgot your password?</a>' + (
            f'<a href="{base}/signup{suffix}">Create an account</a>' if signup_enabled else ""
        )
        extra = (
            '<button class="ghost" type="button" id="magic">Email me a sign-in link instead</button>'
            if magic_link_enabled
            else ""
        )
    elif page == "signup":
        fields = (
            '<label>Name<input name="name" type="text" autocomplete="name"></label>'
            '<label>Email<input name="email" type="email" autocomplete="email" required autofocus></label>'
            '<label>Password<input name="password" type="password" autocomplete="new-password" required minlength="8"></label>'
            '<button class="primary" type="submit">Create account</button>'
        )
        links = f'<a href="{base}/login{suffix}">I already have an account</a>'
        extra = ""
    else:
        fields = (
            '<label>Email<input name="email" type="email" autocomplete="email" required autofocus></label>'
            '<button class="primary" type="submit">Send the link</button>'
        )
        links = f'<a href="{base}/login{suffix}">Back to sign in</a>'
        extra = ""

    legal = " · ".join(
        f'<a href="{_e(b[key])}" target="_blank" rel="noopener noreferrer">{label}</a>'
        for key, label in (("terms_url", "Terms"), ("privacy_url", "Privacy"))
        if b[key]
    )
    footer_text = _e(b["footer"]) if b["footer"] else ""
    post_path = {"login": "signin", "signup": "signup", "forgot": "forgot"}[page]
    dark_vars = "--bg:#15131f;--card:#1e1b2b;--text:#f3f1fa;--muted:#a8a3bd;--line:#34304a;"
    light_vars = f"--bg:{b['background']};--card:{b['card']};--text:{b['text']};--muted:color-mix(in srgb,{b['text']} 60%,{b['card']});--line:color-mix(in srgb,{b['text']} 16%,{b['card']});"
    root = (
        dark_vars if dark else light_vars
    ) + f"--primary:{b['primary']};--radius:{b['radius']}px;--font:{FONTS[b['font']]};"
    media = f"@media (prefers-color-scheme: dark){{:root{{{dark_vars}}}}}" if auto else ""

    script = f"""
const base = {json.dumps(base)};
const carried = {json.dumps(pairs)};
const form = document.getElementById('form');
const note = document.getElementById('note');
const preview = {json.dumps(preview)};
let mfa = null;
function say(text, bad) {{ note.textContent = text; note.className = 'note ' + (bad ? 'bad' : 'ok'); }}
async function post(path, body) {{
  const r = await fetch(base + path, {{ method: 'POST', headers: {{ 'content-type': 'application/json' }}, body: JSON.stringify({{ ...carried, ...body }}) }});
  const j = await r.json().catch(() => ({{}}));
  if (!r.ok) {{ const d = j.detail; throw new Error(typeof d === 'string' ? d : (d && (d.message || (d.problems || []).join(' '))) || 'Something went wrong. Try again.'); }}
  return j;
}}
function done(j) {{
  if (j.mfa_required) {{ mfa = j.mfa_token; form.innerHTML = '<label>Verification code<input name="code" inputmode="numeric" autocomplete="one-time-code" required autofocus></label><button class="primary" type="submit">Verify</button>'; say('Enter the code from your authenticator app.'); return; }}
  if (j.redirect) {{ location.href = j.redirect; return; }}
  if (j.verify) {{ form.remove(); say('Check your email to confirm your address, then sign in.'); return; }}
  if (j.sent) {{ form.remove(); say(j.message || 'If the address has an account, a message is on its way.'); return; }}
  if (j.signed_in) {{ form.remove(); say('You are signed in. You can close this window.'); }}
}}
form.addEventListener('submit', async (event) => {{
  event.preventDefault();
  if (preview) {{ say('This is a preview. Nothing is sent.'); return; }}
  const data = Object.fromEntries(new FormData(form));
  const button = form.querySelector('button.primary'); button.disabled = true;
  try {{
    if (mfa) done(await post('/signin', {{ mfa_token: mfa, code: data.code }}));
    else done(await post('/{post_path}', data));
  }} catch (error) {{ say(error.message, true); }}
  button.disabled = false;
}});
const magic = document.getElementById('magic');
if (magic) magic.addEventListener('click', async () => {{
  const email = form.querySelector('input[name=email]').value;
  if (!email) {{ say('Enter your email address first.', true); return; }}
  if (preview) {{ say('This is a preview. Nothing is sent.'); return; }}
  try {{ done(await post('/magic', {{ email }})); }} catch (error) {{ say(error.message, true); }}
}});
"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} · {_e(name)}</title>
<style>
:root{{{root}}}{media}
*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%}}
body{{font-family:var(--font);background:var(--bg);color:var(--text);display:grid;place-items:center;padding:24px;-webkit-font-smoothing:antialiased}}
main{{width:100%;max-width:400px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:calc(var(--radius) + 6px);padding:36px 32px 28px;box-shadow:0 18px 50px rgba(20,20,40,.08)}}
.logo{{display:block;max-height:44px;max-width:180px;margin:0 0 22px}}.wordmark{{font-weight:700;font-size:20px;letter-spacing:-.02em;margin:0 0 22px}}
h1{{font-size:24px;line-height:1.2;letter-spacing:-.02em;margin:0 0 6px}}p.sub{{margin:0 0 22px;color:var(--muted);font-size:14.5px;line-height:1.5}}
label{{display:block;font-size:13px;font-weight:600;margin:0 0 14px}}
input{{display:block;width:100%;margin-top:6px;padding:11px 13px;font:inherit;font-size:15px;color:var(--text);background:var(--card);border:1px solid var(--line);border-radius:var(--radius)}}
input:focus{{outline:2px solid color-mix(in srgb,var(--primary) 55%,transparent);outline-offset:1px;border-color:var(--primary)}}
button{{font:inherit;cursor:pointer}}.primary{{width:100%;padding:12px;border:0;border-radius:var(--radius);background:var(--primary);color:#fff;font-weight:600;font-size:15px;margin-top:6px}}
.primary:disabled{{opacity:.6;cursor:wait}}.ghost{{width:100%;margin-top:10px;background:none;border:0;color:var(--muted);font-size:13.5px;padding:8px}}
.social{{display:block;text-align:center;padding:11px;margin-bottom:10px;border:1px solid var(--line);border-radius:var(--radius);color:var(--text);text-decoration:none;font-weight:600;font-size:14.5px}}
.or{{display:flex;align-items:center;gap:12px;color:var(--muted);font-size:12.5px;margin:14px 0}}.or::before,.or::after{{content:"";flex:1;height:1px;background:var(--line)}}
.links{{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:18px;font-size:13.5px}}.links a{{color:var(--primary);text-decoration:none;font-weight:600}}
.note{{min-height:20px;font-size:13.5px;margin:12px 0 0}}.note.bad{{color:#c2413b}}.note.ok{{color:var(--muted)}}
.legal{{text-align:center;margin-top:18px;font-size:12.5px;color:var(--muted)}}.legal a{{color:var(--muted)}}
{".preview{position:fixed;top:10px;right:12px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}" if preview else ""}
</style></head>
<body>{'<div class="preview">Preview</div>' if preview else ""}
<main>
  <div class="card">
    {logo}
    <h1>{_e(title)}</h1>
    {f'<p class="sub">{_e(subtitle)}</p>' if subtitle else ""}
    {social}{divider if page != "forgot" else ""}
    <form id="form" novalidate>{fields}</form>
    {extra}
    <div id="note" class="note" role="status" aria-live="polite"></div>
    <div class="links">{links}</div>
  </div>
  <div class="legal">{legal}{" · " if legal and footer_text else ""}{footer_text}</div>
</main>
<script nonce="{_e(nonce)}">{script}</script>
</body></html>"""
