"""Receipts — the paper kind, drawn as an image and a PDF.

A receipt is built from the order as it was when it was paid: the line titles
and prices are the copies on `OrderItem`, not the product's current ones, so a
renamed or repriced product never rewrites a receipt a customer already holds.

It is drawn with Pillow rather than converted from HTML. That keeps it a real
image — sharp at any zoom, identical in every viewer, attachable to an email —
with no browser, no headless Chrome and no network involved. The PDF is the same
drawing on a receipt-sized page.

The look is a till roll: monospaced figures, dashed rules, a torn top and
bottom edge, a PAID stamp and a barcode made from the order's reference.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .. import q
from ..money import format_money

__all__ = ["Receipt", "build", "render_pdf", "render_png"]

FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"


@dataclass
class Line:
    title: str
    variant: str | None
    quantity: int
    unit: str
    total: str


@dataclass
class Receipt:
    store_name: str
    store_email: str | None
    store_lines: list[str]
    number: int
    placed: str
    email: str
    method: str
    reference: str
    paid: bool
    lines: list[Line] = field(default_factory=list)
    subtotal: str = ""
    discount: str | None = None
    shipping: str | None = None
    tax: str | None = None
    total: str = ""
    refunded: str | None = None
    currency: str = ""

    def as_prop(self) -> dict:
        return {
            "store_name": self.store_name,
            "store_email": self.store_email,
            "store_lines": self.store_lines,
            "number": self.number,
            "placed": self.placed,
            "email": self.email,
            "method": self.method,
            "reference": self.reference,
            "paid": self.paid,
            "lines": [line.__dict__ for line in self.lines],
            "subtotal": self.subtotal,
            "discount": self.discount,
            "shipping": self.shipping,
            "tax": self.tax,
            "total": self.total,
            "refunded": self.refunded,
        }


def _when(value: Any) -> str:
    moment = q.parse_dt(value)
    return moment.strftime("%d %b %Y, %H:%M") if moment else ""


async def build(db: Any, order: q.Row) -> Receipt:
    """The receipt for one order, from the order as it was when it was paid (line copies, not the product's current title or price)."""
    store = await q.get(db, "stores", order.store_id)
    items = await q.find(db, "order_items", {"order_id": order.pk}, order="id")
    payment = await q.first(db, "payments", {"order_id": order.pk, "status": "succeeded"}, order="id DESC")
    money = lambda minor: format_money(minor, order.currency)  # noqa: E731
    address = [part for part in (store.address_line1, store.city, store.province) if part]
    return Receipt(
        store_name=store.name, store_email=store.support_email or store.email, store_lines=[", ".join(address)] if address else [],
        number=order.number, placed=_when(order.paid_at or order.placed_at or order.created_at), email=order.email,
        method=(payment.provider if payment else "—").title(),
        reference=(payment.reference if payment and payment.reference else f"order-{order.number}"), paid=order.is_paid,
        lines=[Line(title=i.title, variant=i.variant_title, quantity=i.quantity, unit=money(i.unit_price_minor), total=money(i.total_minor)) for i in items],
        subtotal=money(order.subtotal_minor), discount=money(-order.discount_minor) if order.discount_minor else None,
        shipping=money(order.shipping_minor) if order.shipping_minor else None, tax=money(order.tax_minor) if order.tax_minor else None,
        total=money(order.total_minor), refunded=money(order.refunded_minor) if order.refunded_minor else None, currency=order.currency)


# -- drawing --------------------------------------------------------------

INK = (20, 20, 20)
MUTED = (112, 106, 100)
FAINT = (190, 182, 174)
PAPER = (255, 255, 255)
GROUND = (236, 228, 226)
SAGE = (79, 127, 102)

W = 480  # logical width of the paper, in px before scaling
PAD = 34
SCALE = 2.5


def _font(name: str, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), int(size * SCALE))


class _Pen:
    def __init__(self) -> None:
        self.regular = _font("IBMPlexMono-Regular.ttf", 13)
        self.small = _font("IBMPlexMono-Regular.ttf", 11)
        self.bold = _font("IBMPlexMono-SemiBold.ttf", 13)
        self.big = _font("IBMPlexMono-SemiBold.ttf", 24)
        self.name = _font("Inter-Bold.ttf", 24)
        self.stamp = _font("Inter-Bold.ttf", 30)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _paint(receipt: Receipt, canvas_h: int) -> tuple[Image.Image, float]:
    """Draw the receipt on a canvas of `canvas_h` px; return it and where the
    content ended. Called twice — once tall, to measure, then at the exact height
    so the torn bottom edge lands under the last line."""
    pen = _Pen()
    canvas_w = int((W + 60) * SCALE)
    img = Image.new("RGB", (canvas_w, canvas_h), GROUND)
    draw = ImageDraw.Draw(img)
    inner = (W - PAD * 2) * SCALE

    ox = 30 * SCALE
    paper_top, paper_bottom = 30 * SCALE, canvas_h - 30 * SCALE
    tooth = 9 * SCALE
    xs = list(range(int(ox), int(ox + W * SCALE) + 1, int(tooth)))
    top = [(x, paper_top + (tooth * 0.55 if i % 2 else 0)) for i, x in enumerate(xs)]
    bottom = [(x, paper_bottom - (tooth * 0.55 if i % 2 else 0)) for i, x in enumerate(xs)]
    draw.polygon(top + bottom[::-1], fill=PAPER)

    y = paper_top + 44 * SCALE
    cx = W / 2

    def text(x: float, yy: float, s: str, font, fill=INK, anchor="la") -> None:
        draw.text((ox + x * SCALE, yy), s, font=font, fill=fill, anchor=anchor)

    def rule(color=FAINT, weight=1.2) -> None:
        x, end, dash, gap = ox + PAD * SCALE, ox + (W - PAD) * SCALE, 7 * SCALE, 5 * SCALE
        while x < end:
            draw.line([(x, y), (min(x + dash, end), y)], fill=color, width=max(1, int(weight * SCALE)))
            x += dash + gap

    # -- header
    text(cx, y, receipt.store_name.upper(), pen.name, anchor="mt")
    y += 38 * SCALE
    for line in receipt.store_lines:
        text(cx, y, line, pen.small, MUTED, anchor="mt")
        y += 18 * SCALE
    if receipt.store_email:
        text(cx, y, receipt.store_email, pen.small, MUTED, anchor="mt")
        y += 18 * SCALE
    y += 14 * SCALE
    rule()
    y += 22 * SCALE

    # -- meta
    for label, value in (("RECEIPT", f"#{receipt.number}"), ("DATE", receipt.placed), ("EMAIL", receipt.email), ("PAID VIA", receipt.method)):
        parts = _wrap(draw, value, pen.regular, inner * 0.62)
        text(PAD, y, label, pen.small, MUTED)
        for k, part in enumerate(parts):
            text(W - PAD, y + k * 18 * SCALE, part, pen.regular, anchor="ra")
        y += 24 * SCALE + (len(parts) - 1) * 18 * SCALE
    y += 6 * SCALE
    rule()
    y += 24 * SCALE

    # -- lines
    for line in receipt.lines:
        parts = _wrap(draw, line.title, pen.bold, inner * 0.62)
        for k, part in enumerate(parts):
            text(PAD, y + k * 20 * SCALE, part, pen.bold)
        text(W - PAD, y, line.total, pen.bold, anchor="ra")
        y += 20 * SCALE * len(parts)
        if line.variant:
            text(PAD, y, line.variant, pen.small, MUTED)
            y += 17 * SCALE
        text(PAD, y, f"{line.quantity} x {line.unit}", pen.small, MUTED)
        y += 28 * SCALE
    rule()
    y += 24 * SCALE

    # -- totals
    def row(label: str, value: str) -> None:
        nonlocal y
        text(PAD, y, label, pen.regular, MUTED)
        text(W - PAD, y, value, pen.regular, anchor="ra")
        y += 24 * SCALE

    row("Subtotal", receipt.subtotal)
    if receipt.discount:
        row("Discount", receipt.discount)
    if receipt.shipping:
        row("Shipping", receipt.shipping)
    if receipt.tax:
        row("Tax", receipt.tax)
    y += 6 * SCALE
    rule(INK, 1.6)
    y += 22 * SCALE
    text(PAD, y + 8 * SCALE, "TOTAL", pen.bold)
    text(W - PAD, y, receipt.total, pen.big, anchor="ra")
    y += 46 * SCALE
    if receipt.refunded:
        row("Refunded", f"-{receipt.refunded}")
    y += 10 * SCALE

    # -- reference, and the stamp beside it
    text(PAD, y + 4 * SCALE, "REF", pen.small, MUTED)
    ref = receipt.reference
    text(PAD, y + 22 * SCALE, ref if len(ref) <= 24 else ref[:11] + "…" + ref[-10:], pen.small)
    if receipt.paid:
        label = "PAID"
        tw = draw.textlength(label, font=pen.stamp)
        pad_x, pad_y = 20 * SCALE, 8 * SCALE
        stamp = Image.new("RGBA", (int(tw + pad_x * 2), int(pen.stamp.size + pad_y * 2)), (0, 0, 0, 0))
        sd = ImageDraw.Draw(stamp)
        sd.rounded_rectangle([0, 0, stamp.width - 1, stamp.height - 1], radius=10 * SCALE, outline=SAGE, width=int(3 * SCALE))
        sd.text((stamp.width / 2, stamp.height / 2), label, font=pen.stamp, fill=SAGE, anchor="mm")
        stamp = stamp.rotate(-8, expand=True, resample=Image.BICUBIC)
        img.paste(stamp, (int(ox + (W - PAD) * SCALE - stamp.width), int(y - 8 * SCALE)), stamp)
    y += 70 * SCALE
    rule()
    y += 26 * SCALE

    # -- barcode and thanks
    digest = hashlib.sha256(receipt.reference.encode()).digest() * 4
    x, right = ox + (PAD + 24) * SCALE, ox + (W - PAD - 24) * SCALE
    i = 0
    while x < right:
        bar = (1 + digest[i % len(digest)] % 4) * SCALE
        if i % 2 == 0:
            draw.rectangle([x, y, min(x + bar, right), y + 46 * SCALE], fill=INK)
        x += bar + (1 + digest[(i + 7) % len(digest)] % 2) * SCALE
        i += 1
    y += 66 * SCALE
    text(cx, y, "Thank you for your order", pen.regular, anchor="mt")
    y += 22 * SCALE
    text(cx, y, "Keep this receipt as proof of purchase.", pen.small, MUTED, anchor="mt")
    y += 22 * SCALE
    return img, y


def _draw(receipt: Receipt) -> Image.Image:
    _, end = _paint(receipt, 6000)
    exact = int(end + 30 * SCALE + 30 * SCALE)
    return _paint(receipt, exact)[0]


def render_png(receipt: Receipt) -> bytes:
    buffer = io.BytesIO()
    _draw(receipt).save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def render_pdf(receipt: Receipt) -> bytes:
    """The same drawing on a page as wide as a till roll (about 4 inches)."""
    image = _draw(receipt)
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=image.width / 4.0, title=f"Receipt #{receipt.number}")
    return buffer.getvalue()
