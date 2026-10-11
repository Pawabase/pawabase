"""The page people see when they open the gateway's address in a browser."""

from __future__ import annotations

from html import escape

from sillo import HttpContext, SilloApp, html

from pawabase_server.settings import Settings

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{name}</title>
<style>:root{{color-scheme:light dark}}body{{margin:0;min-height:100vh;display:grid;place-items:center;font:15px/1.6 system-ui,sans-serif}}
main{{max-width:30rem;padding:2rem;text-align:center}}h1{{margin:.5rem 0}}p{{color:gray}}code{{background:rgba(127,127,127,.15);padding:.1rem .4rem;border-radius:6px}}
.dot{{display:inline-block;width:.6rem;height:.6rem;border-radius:50%;background:#2e8a5e;margin-right:.4rem}}</style></head>
<body><main><h1>{name}</h1><p><span class="dot"></span>This project's API is running.</p>
<p>Send requests with your project's API key in the <code>apikey</code> header, for example <code>/auth/v1/signup</code>, <code>/rest/v1/…</code> or <code>/functions/v1/…</code>.</p>
<p><a href="/v1/status">Service status</a></p></main></body></html>"""


def register(app: SilloApp, settings: Settings) -> None:
    @app.get("/", exclude_from_schema=True)
    async def landing(ctx: HttpContext):
        """The API itself lives under /auth/v1, /rest/v1, /functions/v1 and the other prefixes; this is only a signpost."""
        return html(PAGE.format(name=escape(settings.project_name)))
