"""The sign-in page of a self-hosted Studio.

It is a single self-contained document: the gate shows it before Studio's front end has been served, so it cannot lean on that bundle.
Its look follows Pawabase Cloud's sign-in (a tinted canvas, one white card, a dark pill button, the violet focus ring), in both colour
schemes, and it needs no more than a few lines of script (show/hide the password, and a busy state while the form is sent).
"""

from __future__ import annotations

from html import escape

CSS = """
:root{color-scheme:light dark;--canvas:#ebe8f4;--card:#fff;--ink:#1f1b2e;--muted:#6b6580;--line:#d9d5e4;--brand:#6d5bd0;--ring:#d9d2f7;
--link:#5b45c9;--btn:#1f1b2e;--btn-ink:#fff;--btn-hover:#000;--alert-bg:#fdecea;--alert-ink:#a3271a;--alert-line:#f6c7c1;--input:#fff}
@media(prefers-color-scheme:dark){:root{--canvas:#0d0d12;--card:#1f1d28;--ink:#f1eef7;--muted:#a39cb4;--line:#3a3547;--brand:#9d8cff;
--ring:#3b3566;--link:#cdbfff;--btn:#f1eefa;--btn-ink:#1f1b2e;--btn-hover:#fff;--alert-bg:#3a1d1d;--alert-ink:#ffb4ab;--alert-line:#5c2b2b;--input:#1a1822}}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;display:grid;place-items:center;padding:24px;background:var(--canvas);color:var(--ink);
font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
main{width:100%;max-width:440px;padding:36px 34px 30px;border-radius:28px;background:var(--card);display:flex;flex-direction:column;gap:18px}
.brand{display:flex;align-items:center;gap:10px}
.brand img{width:34px;height:34px}
.brand b{display:block;font-size:20px;font-weight:700;letter-spacing:-.035em;line-height:1.05}
.brand small{display:block;font-size:11.5px;font-weight:650;color:var(--muted);letter-spacing:.02em}
h1{margin:6px 0 0;font-size:24px;font-weight:700;letter-spacing:-.03em;line-height:1.2}
.lead{margin:-10px 0 0;color:var(--muted)}
form{display:flex;flex-direction:column;gap:14px;margin:0}
label{display:block;margin-bottom:6px;font-size:13.5px;font-weight:600}
.field{position:relative}
input{width:100%;height:42px;padding:0 14px;border:1px solid var(--line);border-radius:12px;background:var(--input);color:var(--ink);
font:inherit;outline:none;transition:border-color .15s,outline-color .15s}
input:focus-visible{border-color:var(--brand);outline:3px solid var(--ring)}
input[aria-invalid=true]{border-color:var(--alert-ink)}
.field.has-toggle input{padding-right:64px}
.toggle{position:absolute;right:6px;top:50%;transform:translateY(-50%);height:30px;padding:0 10px;border:0;border-radius:8px;background:transparent;
color:var(--link);font:inherit;font-size:13px;font-weight:650;cursor:pointer}
.toggle:hover{background:var(--canvas)}
.toggle:focus-visible{outline:2px solid var(--brand)}
button.primary{height:42px;border:0;border-radius:999px;background:var(--btn);color:var(--btn-ink);font:inherit;font-weight:650;cursor:pointer;
transition:background .15s,opacity .15s}
button.primary:hover{background:var(--btn-hover)}
button.primary:focus-visible{outline:3px solid var(--ring);outline-offset:2px}
button.primary[disabled]{opacity:.65;cursor:progress}
.alert{padding:10px 12px;border:1px solid var(--alert-line);border-radius:12px;background:var(--alert-bg);color:var(--alert-ink);font-size:13.5px}
.foot{text-align:center;color:var(--muted);font-size:13px}
@media(max-width:480px){body{padding:12px}main{padding:28px 22px 24px;border-radius:24px}}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""

SCRIPT = """
(function(){
  var form=document.getElementById('login'),pw=document.getElementById('password'),tog=document.getElementById('toggle');
  tog.hidden=false;
  tog.addEventListener('click',function(){
    var show=pw.type==='password';
    pw.type=show?'text':'password';
    tog.textContent=show?'Hide':'Show';
    tog.setAttribute('aria-pressed',String(show));
    pw.focus();
  });
  form.addEventListener('submit',function(){
    var b=form.querySelector('button.primary');
    b.disabled=true;b.textContent='Signing in\\u2026';
  });
  var a=document.querySelector('.alert');
  if(a){document.getElementById(a.dataset.focus||'password').focus();}
})();
"""


def render(*, logo: str, error: str = "", username: str = "", retry_after: int = 0) -> bytes:
    """The sign-in document. ``error`` and ``username`` are escaped here; callers pass them as they are."""
    notice = ""
    if error:
        wait = (
            f" Try again in {max(1, retry_after // 60 + (1 if retry_after % 60 else 0))} minute(s)."
            if retry_after
            else ""
        )
        notice = f'<div class="alert" role="alert" data-focus="{"username" if retry_after else "password"}">{escape(error)}{wait}</div>'
    invalid = ' aria-invalid="true"' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Sign in · Pawabase Studio</title><style>{CSS}</style></head>
<body><main>
<div class="brand"><img src="{escape(logo, quote=True)}" alt="Pawabase" width="34" height="34"><div><b>pawabase</b><small>Studio</small></div></div>
<h1>Sign in to Studio</h1>
<p class="lead">Sign in to manage this self-hosted runtime.</p>
{notice}
<form id="login" method="post" action="/login">
<div><label for="username">Username</label>
<input id="username" name="username" value="{escape(username, quote=True)}" autocomplete="username" autocapitalize="none" spellcheck="false"{invalid}
 required {"" if error else "autofocus"}></div>
<div><label for="password">Password</label>
<div class="field has-toggle"><input id="password" name="password" type="password" autocomplete="current-password"{invalid} required>
<button id="toggle" class="toggle" type="button" aria-pressed="false" aria-controls="password" hidden>Show</button></div></div>
<button class="primary" type="submit">Sign in</button>
</form>
<p class="foot">Credentials are set by whoever runs this installation.</p>
</main><script>{SCRIPT}</script></body></html>""".encode()
