"""Email templates, built as tables.

No Jinja, and no flexbox. Two deliberate choices:

**Tables, not CSS layout.** Outlook renders HTML with Word's engine, which
supports neither flexbox nor grid and ignores most of `<style>`. A layout that
looks right in a browser and collapses into an unstyled column for a third of
recipients is a layout that does not work. Every style here is inline, and
every structure is a table.

**Python, not a template engine.** These are five emails with a shared shell.
Adding a template engine to render five files would be a dependency, a
directory, and a second place for a typo to hide — and the escaping still has
to be right either way.

Every template returns `(html, text, subject)`. The plain-text part is not an
afterthought: it is what a screen reader, a smartwatch, and every spam filter
that penalises HTML-only mail actually read.
"""

from __future__ import annotations

import html as html_escape
from typing import Any

from types import SimpleNamespace

# Set by `render` for the duration of one (synchronous) render: the platform settings of
# the environment the email is for. Nothing awaits between setting and using it.
config: Any = SimpleNamespace(app_url='', storefront_suffix='', app_name='SELL4ME', is_local=True)

__all__ = ["TEMPLATES", "render"]

# The palette matches the dashboard's, muted for email — a plum accent on warm
# neutrals, which reads as considered rather than as a system notification.
INK = "#2c2521"
MUTED = "#7c7269"
LINE = "#e9e3dc"
CANVAS = "#f7f4f0"
BRAND = "#8d3a63"
POSITIVE = "#2f7d52"


def _shell(*, title: str, preheader: str, body: str, store_name: str, footer: str = "") -> str:
    """The wrapper every email shares.

    The preheader is the grey line a client shows next to the subject in the
    inbox list. Left unset, clients scrape the first text they find — usually
    "View this in your browser", which wastes the one line a recipient reads
    before deciding whether to open anything.
    """
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<title>{html_escape.escape(title)}</title>
</head>
<body style="margin:0;padding:0;background:{CANVAS};">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">
    {html_escape.escape(preheader)}
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:{CANVAS};padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="max-width:560px;background:#ffffff;border:1px solid {LINE};border-radius:14px;overflow:hidden;">

        <tr><td style="padding:26px 30px 0 30px;">
          <span style="font:600 15px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
                       color:{INK};letter-spacing:-0.01em;">
            {html_escape.escape(store_name)}
          </span>
        </td></tr>

        <tr><td style="padding:22px 30px 30px 30px;
                       font:400 15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
                       color:{INK};">
          {body}
        </td></tr>

      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="max-width:560px;">
        <tr><td style="padding:18px 30px;text-align:center;
                       font:400 12px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
                       color:{MUTED};">
          {footer or f"Sent by {html_escape.escape(store_name)}."}
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _heading(text: str) -> str:
    return (
        f'<h1 style="margin:0 0 10px 0;font:600 21px/1.3 -apple-system,BlinkMacSystemFont,'
        f"'Segoe UI',Roboto,sans-serif;color:{INK};letter-spacing:-0.02em;\">"
        f"{html_escape.escape(text)}</h1>"
    )


def _paragraph(text: str, *, muted: bool = False) -> str:
    colour = MUTED if muted else INK
    return f'<p style="margin:0 0 14px 0;color:{colour};">{text}</p>'


def _button(label: str, url: str) -> str:
    """A bulletproof button.

    A table with a background, not a styled `<a>`: Outlook drops padding and
    background on anchors, and the result is bare blue underlined text where
    the call to action should be.
    """
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0"
       style="margin:22px 0;">
  <tr><td style="background:{INK};border-radius:999px;">
    <a href="{html_escape.escape(url, quote=True)}"
       style="display:inline-block;padding:11px 26px;color:#ffffff;text-decoration:none;
              font:500 14px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
      {html_escape.escape(label)}
    </a>
  </td></tr>
</table>"""


def _line_items(items: list[dict[str, Any]]) -> str:
    rows = []
    for item in items:
        variant = (
            f'<span style="color:{MUTED};"> · {html_escape.escape(str(item["variant"]))}</span>'
            if item.get("variant")
            else ""
        )
        rows.append(
            f"""<tr>
  <td style="padding:9px 0;border-bottom:1px solid {LINE};font-size:14px;">
    {html_escape.escape(str(item["title"]))}{variant}
    <span style="color:{MUTED};"> × {int(item.get("quantity", 1))}</span>
  </td>
  <td style="padding:9px 0;border-bottom:1px solid {LINE};font-size:14px;
             text-align:right;white-space:nowrap;">
    {html_escape.escape(str(item.get("total", "")))}
  </td>
</tr>"""
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:18px 0;">{"".join(rows)}</table>'
    )


def _totals(rows: list[tuple[str, str, bool]]) -> str:
    """`[(label, value, is_total)]` as a right-aligned summary block."""
    out = []
    for label, value, is_total in rows:
        weight = "600" if is_total else "400"
        colour = INK if is_total else MUTED
        border = f"border-top:1px solid {LINE};padding-top:10px;" if is_total else ""
        out.append(
            f"""<tr>
  <td style="padding:3px 0;{border}color:{colour};font-size:14px;font-weight:{weight};">
    {html_escape.escape(label)}
  </td>
  <td style="padding:3px 0;{border}text-align:right;color:{INK};font-size:14px;
             font-weight:{weight};white-space:nowrap;">
    {html_escape.escape(value)}
  </td>
</tr>"""
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'{"".join(out)}</table>'
    )


def _address(address: dict[str, Any] | None) -> str:
    if not address:
        return ""
    parts = [
        address.get("name"),
        address.get("line1"),
        address.get("line2"),
        " ".join(filter(None, [address.get("city"), address.get("postal_code")])),
        address.get("country"),
    ]
    lines = "<br>".join(html_escape.escape(str(p)) for p in parts if p)
    return (
        f'<div style="margin:18px 0;padding:14px;background:{CANVAS};border-radius:10px;'
        f'font-size:13px;line-height:1.6;color:{MUTED};">'
        f'<strong style="color:{INK};display:block;margin-bottom:4px;">Delivering to</strong>'
        f"{lines}</div>"
    )


def _shop_url(slug: str, path: str = "") -> str:
    scheme = "https" if str(config.app_url).lower().startswith("https://") else "http"
    return f"{scheme}://{slug}.{config.storefront_suffix}{path}"


# -- the templates ----------------------------------------------------------


def order_receipt(ctx: dict[str, Any]) -> tuple[str, str, str]:
    store = ctx["store"]
    order = ctx["order"]
    items = ctx.get("items", [])

    totals = [("Subtotal", order["subtotal"], False)]
    if order.get("has_discount"):
        totals.append(("Discount", f"−{order['discount']}", False))
    totals.append(("Shipping", order["shipping"], False))
    totals.append(("Total", order["total"], True))

    body = (
        _heading("Thank you for your order")
        + _paragraph(
            f"Order <strong>#{order['number']}</strong> is confirmed. "
            f"We will email you again the moment it ships."
        )
        + _line_items(items)
        + _totals(totals)
        + _address(ctx.get("address"))
        + _button("Track your order", _shop_url(store["slug"], order["status_url"]))
        + _paragraph(
            "Keep that link — it is how you check this order without an account.",
            muted=True,
        )
    )

    text = "\n".join(
        [
            "Thank you for your order",
            "",
            f"Order #{order['number']} is confirmed.",
            "",
            *[
                f"  {item['title']} x{item['quantity']}  {item['total']}"
                for item in items
            ],
            "",
            f"  Subtotal  {order['subtotal']}",
            f"  Shipping  {order['shipping']}",
            f"  Total     {order['total']}",
            "",
            f"Track it: {_shop_url(store['slug'], order['status_url'])}",
        ]
    )

    return (
        _shell(
            title=f"Order #{order['number']}",
            preheader=f"Your order is confirmed — {order['total']}",
            body=body,
            store_name=store["name"],
            footer=_support(store),
        ),
        text,
        f"Your {store['name']} order #{order['number']}",
    )


def order_shipped(ctx: dict[str, Any]) -> tuple[str, str, str]:
    store = ctx["store"]
    order = ctx["order"]

    tracking = ""
    if order.get("tracking_number"):
        tracking = (
            f'<div style="margin:18px 0;padding:14px;background:{CANVAS};border-radius:10px;">'
            f'<div style="font-size:12px;color:{MUTED};margin-bottom:2px;">Tracking number</div>'
            f'<div style="font:600 15px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:{INK};">'
            f'{html_escape.escape(str(order["tracking_number"]))}</div></div>'
        )

    url = order.get("tracking_url") or _shop_url(store.get("slug", ""), order["status_url"])
    body = (
        _heading("Your order is on its way")
        + _paragraph(f"Order <strong>#{order['number']}</strong> has left us.")
        + tracking
        + _button("Track your parcel", url)
    )

    text = (
        f"Your order is on its way\n\n"
        f"Order #{order['number']} has shipped.\n"
        + (f"Tracking: {order['tracking_number']}\n" if order.get("tracking_number") else "")
        + f"\n{url}\n"
    )

    return (
        _shell(
            title="Your order has shipped",
            preheader=f"Order #{order['number']} is on its way",
            body=body,
            store_name=store["name"],
            footer=_support(store),
        ),
        text,
        f"Your {store['name']} order is on its way",
    )


def order_refunded(ctx: dict[str, Any]) -> tuple[str, str, str]:
    store = ctx["store"]
    order = ctx["order"]
    refund = ctx["refund"]

    reason = (
        _paragraph(f"Reason: {html_escape.escape(str(refund['reason']))}", muted=True)
        if refund.get("reason")
        else ""
    )
    body = (
        _heading("Your refund is on its way")
        + _paragraph(
            f"We have refunded <strong>{html_escape.escape(refund['amount'])}</strong> "
            f"on order #{order['number']}."
        )
        + reason
        + _paragraph(
            "Depending on your bank it can take five to ten days to appear on "
            "your statement.",
            muted=True,
        )
    )

    text = (
        f"Your refund is on its way\n\n"
        f"We have refunded {refund['amount']} on order #{order['number']}.\n"
        + (f"Reason: {refund['reason']}\n" if refund.get("reason") else "")
        + "\nIt can take five to ten days to appear on your statement.\n"
    )

    return (
        _shell(
            title="Refund issued",
            preheader=f"{refund['amount']} refunded on order #{order['number']}",
            body=body,
            store_name=store["name"],
            footer=_support(store),
        ),
        text,
        f"Refund for {store['name']} order #{order['number']}",
    )


def abandoned_cart(ctx: dict[str, Any]) -> tuple[str, str, str]:
    store = ctx["store"]
    items = ctx.get("items", [])
    url = _shop_url(store["slug"], ctx["recover_url"])

    body = (
        _heading("You left something behind")
        + _paragraph("Your basket is still here. Pick up exactly where you left off.")
        + _line_items(
            [
                {"title": item["title"], "variant": item.get("variant"), "quantity": item["quantity"], "total": ""}
                for item in items
            ]
        )
        + _button("Return to your basket", url)
        + _paragraph(
            "Items are not reserved, so anything popular may sell out.", muted=True
        )
    )

    text = (
        "You left something behind\n\n"
        "Your basket is still here:\n\n"
        + "\n".join(f"  {item['title']} x{item['quantity']}" for item in items)
        + f"\n\nReturn to it: {url}\n"
    )

    return (
        _shell(
            title="Your basket is waiting",
            preheader=f"Still thinking about it? Your basket is worth {ctx.get('value', '')}",
            body=body,
            store_name=store["name"],
        ),
        text,
        f"You left something at {store['name']}",
    )


def staff_invitation(ctx: dict[str, Any]) -> tuple[str, str, str]:
    store = ctx["store"]
    url = f"{config.app_url}{ctx['accept_url']}"
    inviter = ctx.get("invited_by")

    body = (
        _heading(f"Join {store['name']}")
        + _paragraph(
            (f"{html_escape.escape(str(inviter))} has invited you" if inviter else "You have been invited")
            + f" to work on <strong>{html_escape.escape(store['name'])}</strong> "
            f"as {html_escape.escape(str(ctx['role']))}."
        )
        + _button("Accept the invitation", url)
        + _paragraph("The link expires in seven days.", muted=True)
    )

    text = (
        f"Join {store['name']}\n\n"
        f"You have been invited to work on {store['name']} as {ctx['role']}.\n\n"
        f"Accept: {url}\n\nThe link expires in seven days.\n"
    )

    return (
        _shell(
            title=f"Join {store['name']}",
            preheader=f"You have been invited as {ctx['role']}",
            body=body,
            store_name=store["name"],
        ),
        text,
        f"You have been invited to {store['name']}",
    )


def store_welcome(ctx: dict[str, Any]) -> tuple[str, str, str]:
    store = ctx["store"]

    body = (
        _heading(f"{store['name']} is ready")
        + _paragraph(
            f"Hello {html_escape.escape(str(ctx.get('name', 'there')))} — your store exists. "
            f"Four things stand between you and your first sale."
        )
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:18px 0;">'
        + "".join(
            f"""<tr><td style="padding:8px 0;border-bottom:1px solid {LINE};font-size:14px;">
  <span style="color:{BRAND};font-weight:600;">{index}.</span>&nbsp; {html_escape.escape(step)}
</td></tr>"""
            for index, step in enumerate(
                [
                    "Add your first product",
                    "Connect Stripe or Paystack",
                    "Set up shipping",
                    "Customise your storefront",
                ],
                start=1,
            )
        )
        + "</table>"
        + _button("Open your dashboard", f"{config.app_url}/")
        + _paragraph(
            "No monthly fee. We keep 5% of each successful sale, and nothing "
            "when you do not sell.",
            muted=True,
        )
    )

    text = (
        f"{store['name']} is ready\n\n"
        "Four things stand between you and your first sale:\n"
        "  1. Add your first product\n"
        "  2. Connect Stripe or Paystack\n"
        "  3. Set up shipping\n"
        "  4. Customise your storefront\n\n"
        f"{config.app_url}/\n"
    )

    return (
        _shell(
            title=f"{store['name']} is ready",
            preheader="Four steps to your first sale",
            body=body,
            store_name=config.app_name,
        ),
        text,
        f"{store['name']} is ready",
    )


def password_reset(ctx: dict[str, Any]) -> tuple[str, str, str]:
    url = ctx["reset_url"]
    name = ctx.get("name") or "there"

    body = (
        _heading("Reset your password")
        + _paragraph(f"Hello {html_escape.escape(str(name))} — use the button below to choose a new password.")
        + _button("Reset password", url)
        + _paragraph(
            "This link works once and expires in an hour. If you did not ask to "
            "reset your password, you can ignore this email — nothing changes "
            "until you open a new one.",
            muted=True,
        )
    )

    text = (
        "Reset your password\n\n"
        f"Use this link to choose a new password: {url}\n\n"
        "This link works once and expires in an hour. If you did not ask to "
        "reset your password, ignore this email.\n"
    )

    return (
        _shell(
            title="Reset your password",
            preheader="Choose a new password for your account",
            body=body,
            store_name=config.app_name,
        ),
        text,
        f"Reset your {config.app_name} password",
    )


def help_reply(ctx: dict[str, Any]) -> tuple[str, str, str]:
    store = ctx["store"]
    name = ctx.get("name") or "there"
    reply = str(ctx["reply"])
    question = ctx.get("question")
    url = f"{config.app_url}{ctx['ticket_url']}"

    quoted = (
        _paragraph(f"<em>You asked:</em> {html_escape.escape(str(question))}", muted=True)
        if question
        else ""
    )

    body = (
        _heading(f"{store['name']} replied")
        + _paragraph(f"Hello {html_escape.escape(str(name))} —")
        + quoted
        + _paragraph(html_escape.escape(reply).replace("\n", "<br>"))
        + _button("View the conversation", url)
    )

    text = (
        f"{store['name']} replied\n\n"
        + (f"You asked: {question}\n\n" if question else "")
        + f"{reply}\n\n"
        f"View the conversation: {url}\n"
    )

    return (
        _shell(
            title=f"{store['name']} replied to your question",
            preheader=reply[:120],
            body=body,
            store_name=store["name"],
        ),
        text,
        f"{store['name']} replied to your question",
    )


def _support(store: dict[str, Any]) -> str:
    email = store.get("support_email")
    if not email:
        return f"Sent by {html_escape.escape(store['name'])}."
    safe = html_escape.escape(str(email))
    return (
        f"Questions? Reply to this email or write to "
        f'<a href="mailto:{safe}" style="color:{BRAND};">{safe}</a>.'
    )


TEMPLATES = {
    "order_receipt": order_receipt,
    "order_shipped": order_shipped,
    "order_refunded": order_refunded,
    "abandoned_cart": abandoned_cart,
    "staff_invitation": staff_invitation,
    "store_welcome": store_welcome,
    "password_reset": password_reset,
    "help_reply": help_reply,
}


def render(template: str, context: dict[str, Any], settings: Any = None) -> tuple[str, str, str]:
    """Render one template to `(html, text, subject)`.

    Raises on an unknown name rather than sending a blank email — a typo in a
    template name should fail the job loudly, and the job's retry will fail the
    same way until someone reads the log.
    """
    builder = TEMPLATES.get(template)
    if builder is None:
        raise KeyError(
            f"No mail template {template!r}. Known: {', '.join(sorted(TEMPLATES))}."
        )
    global config
    if settings is not None:
        config = SimpleNamespace(
            app_url=settings.app_url,
            storefront_suffix=settings.storefront_suffix,
            app_name=getattr(settings, "app_name", "SELL4ME"),
            is_local=not settings.app_url.lower().startswith("https://"),
        )
    return builder(context)
