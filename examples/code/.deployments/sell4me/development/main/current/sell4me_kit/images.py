"""Image derivatives, with Pillow.

An uploaded photo is 4000px of JPEG straight off a phone. Serving that to a
grid of twelve product cards is several megabytes for something rendered at
300px, and it is the single largest thing a storefront can get wrong about its
own speed — which search engines measure and rank on.

So every upload is processed once, in the background, into a small fixed set:

===========  ========  ==========================================================
`1200`       WebP      the product page's main image
`600`        WebP      the listing grid at 2×
`300`        WebP      the cart line and the grid at 1×
`og`         JPEG      1200×630, for link previews
`thumb`      WebP      96px, for the dashboard's own tables
===========  ========  ==========================================================

Four decisions worth stating:

* **WebP for display, JPEG for sharing.** WebP is 25–35% smaller at the same
  quality and is supported everywhere that matters now. The OpenGraph image
  stays JPEG because the crawlers that read it are not browsers and several
  still do not accept WebP.
* **Never upscale.** A 400px source asked for a 1200px derivative gets 400px,
  because inventing pixels makes the file bigger and the image worse.
* **The OG image is cropped, not fitted.** Link previews are a fixed 1.91:1
  frame; letterboxing a portrait photo into it wastes most of the space that
  actually gets seen.
* **EXIF orientation is applied and then stripped.** A phone photo carries a
  rotation flag that browsers honour inconsistently, and the rest of the EXIF
  block carries GPS coordinates a merchant did not mean to publish.

Pillow is CPU-bound and this is an async application, so the whole pipeline
runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("commerce.images")

__all__ = ["DERIVATIVES", "Derivative", "ImageError", "process_upload", "probe"]


class ImageError(Exception):
    """The bytes were not an image this platform can process."""


@dataclass(frozen=True, slots=True)
class Derivative:
    """One size to produce."""

    name: str
    width: int
    height: int | None = None
    fmt: str = "WEBP"
    quality: int = 82
    #: Crop to fill the frame rather than fitting inside it. Only the OG image
    #: wants this — see the module docstring.
    crop: bool = False

    @property
    def extension(self) -> str:
        return {"WEBP": "webp", "JPEG": "jpg", "PNG": "png"}[self.fmt]


DERIVATIVES: tuple[Derivative, ...] = (
    Derivative("1200", 1200),
    Derivative("600", 600),
    Derivative("300", 300),
    Derivative("thumb", 96, quality=78),
    Derivative("og", 1200, height=630, fmt="JPEG", quality=86, crop=True),
)

#: Beyond this, a source is downscaled before anything else happens. A 12000px
#: image is either a mistake or an attempt to exhaust memory — Pillow allocates
#: width × height × 4 bytes to decode, and 12000² is 576MB.
MAX_SOURCE_EDGE = 6000


@dataclass(slots=True)
class ProcessedImage:
    """What one upload produced."""

    width: int
    height: int
    format: str
    #: `{derivative name: (bytes, content type, width, height)}`
    variants: dict[str, tuple[bytes, str, int, int]]
    #: A tiny blurred placeholder, inlined into the page so a card has
    #: something to show before its image arrives.
    placeholder: str
    #: The dominant colour, as a hex string. Used as the image's background
    #: while it loads, so the page does not flash white.
    dominant: str


async def process_upload(data: bytes) -> ProcessedImage:
    """Turn uploaded bytes into every derivative. Runs off the event loop."""
    return await asyncio.to_thread(_process, data)


async def probe(data: bytes) -> tuple[int, int, str]:
    """The dimensions and format of an image, without processing it."""
    return await asyncio.to_thread(_probe, data)


def _pillow() -> Any:
    try:
        from PIL import Image, ImageOps

        return Image, ImageOps
    except ImportError as error:  # pragma: no cover - deployment concern
        raise ImageError(
            "Image processing needs Pillow. Install it, or upload images that "
            "are already sized."
        ) from error


def _probe(data: bytes) -> tuple[int, int, str]:
    Image, _ = _pillow()
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.width, image.height, (image.format or "").upper()
    except Exception as error:  # noqa: BLE001
        raise ImageError("That file is not an image we can read.") from error


def _process(data: bytes) -> ProcessedImage:
    Image, ImageOps = _pillow()

    # Pillow's own bomb guard. Left at its default rather than disabled: a
    # decompression bomb is a real upload vector, and the limit is generous.
    Image.MAX_IMAGE_PIXELS = 80_000_000

    try:
        source = Image.open(io.BytesIO(data))
        source.load()
    except Exception as error:  # noqa: BLE001
        raise ImageError("That file is not an image we can read.") from error

    original_format = (source.format or "JPEG").upper()

    # Apply the EXIF rotation flag, then drop the metadata entirely — see the
    # note in the module docstring about GPS coordinates.
    source = ImageOps.exif_transpose(source)

    if source.mode in ("P", "LA"):
        source = source.convert("RGBA")
    if source.mode == "CMYK":
        source = source.convert("RGB")

    if max(source.size) > MAX_SOURCE_EDGE:
        source.thumbnail((MAX_SOURCE_EDGE, MAX_SOURCE_EDGE), Image.Resampling.LANCZOS)

    width, height = source.size
    variants: dict[str, tuple[bytes, str, int, int]] = {}

    for spec in DERIVATIVES:
        rendered = _render(Image, ImageOps, source, spec)
        if rendered is not None:
            variants[spec.name] = rendered

    return ProcessedImage(
        width=width,
        height=height,
        format=original_format,
        variants=variants,
        placeholder=_placeholder(Image, source),
        dominant=_dominant(source),
    )


def _render(
    Image: Any, ImageOps: Any, source: Any, spec: Derivative
) -> tuple[bytes, str, int, int] | None:
    """Produce one derivative, or `None` if it would be an upscale."""
    if spec.crop and spec.height:
        # Fill the frame: scale to cover, then take the centre.
        frame = ImageOps.fit(
            source, (spec.width, spec.height), Image.Resampling.LANCZOS, centering=(0.5, 0.5)
        )
    else:
        if source.width <= spec.width and spec.name != "og":
            # Never upscale. The source is already smaller than this size, so
            # the derivative would be a bigger file of a worse image.
            return None
        frame = source.copy()
        frame.thumbnail((spec.width, spec.width * 4), Image.Resampling.LANCZOS)

    if spec.fmt == "JPEG" and frame.mode in ("RGBA", "LA", "P"):
        # JPEG has no alpha. Flattening onto white rather than black, because
        # a product shot on transparency is nearly always on a white page.
        background = Image.new("RGB", frame.size, (255, 255, 255))
        background.paste(frame, mask=frame.split()[-1] if frame.mode in ("RGBA", "LA") else None)
        frame = background

    buffer = io.BytesIO()
    options: dict[str, Any] = {"quality": spec.quality, "optimize": True}
    if spec.fmt == "WEBP":
        options["method"] = 4  # a fair trade of encode time for size
    if spec.fmt == "JPEG":
        options["progressive"] = True
        options["subsampling"] = "4:2:0"

    frame.save(buffer, spec.fmt, **options)
    return (
        buffer.getvalue(),
        f"image/{spec.extension if spec.extension != 'jpg' else 'jpeg'}",
        frame.width,
        frame.height,
    )


def _placeholder(Image: Any, source: Any) -> str:
    """A 20px WebP, base64'd, for the blur-up placeholder.

    Inlined into the page rather than fetched: at this size the data URI is
    smaller than the HTTP request that would fetch it, and it is there the
    instant the markup is.
    """
    import base64

    tiny = source.copy()
    tiny.thumbnail((20, 20), Image.Resampling.LANCZOS)
    if tiny.mode not in ("RGB", "RGBA"):
        tiny = tiny.convert("RGB")

    buffer = io.BytesIO()
    tiny.save(buffer, "WEBP", quality=40)
    return f"data:image/webp;base64,{base64.b64encode(buffer.getvalue()).decode()}"


def _dominant(source: Any) -> str:
    """The image's dominant colour, as `#rrggbb`.

    Used as the `<img>` background while it loads. Computed by shrinking to a
    single pixel, which is a mean rather than a mode — good enough for a
    placeholder and far cheaper than quantising.
    """
    try:
        pixel = source.convert("RGB").resize((1, 1)).getpixel((0, 0))
        return "#{:02x}{:02x}{:02x}".format(*pixel[:3])
    except Exception:  # noqa: BLE001
        return "#f5f3f0"


def srcset_for(variants: dict[str, str]) -> str:
    """A `srcset` string from a stored variant map.

    Lets the browser pick — a phone downloads the 300, a desktop the 1200, and
    neither is told which by us.
    """
    parts = []
    for name in ("300", "600", "1200"):
        url = variants.get(name)
        if url:
            parts.append(f"{url} {name}w")
    return ", ".join(parts)


__all__ += ["ProcessedImage", "srcset_for"]
