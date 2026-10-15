"""Image transformations applied at delivery, never to the stored object.

A request such as ``?width=400&height=400&fit=cover&format=webp`` is rendered from the original, bounded by what the bucket allows:
dimensions, input pixels, source and output size, concurrency and time. The result is cached by (object version, options), and the
caller has already passed the object's read policy before this module is reached, so a variant can never be served to someone who may
not read the original.
"""

from __future__ import annotations

import asyncio
import io
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageOps

#: What a bucket's ``transforms`` JSON may set, and what unset means.
DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "formats": ["webp", "avif", "jpeg", "png"],
    "fits": ["contain", "cover", "fill"],
    "auto_format": True,
    "max_width": 4096,
    "max_height": 4096,
    "max_input_pixels": 50_000_000,
    "max_source_bytes": 25 * 1024 * 1024,
    "max_output_bytes": 10 * 1024 * 1024,
    "quality": 80,
    "max_concurrent": 4,
    "timeout_seconds": 10,
}
#: Hard ceilings: a bucket can lower these but never raise them past the platform's.
CEILINGS = {
    "max_width": 8192,
    "max_height": 8192,
    "max_input_pixels": 100_000_000,
    "max_source_bytes": 64 * 1024 * 1024,
    "max_output_bytes": 32 * 1024 * 1024,
    "max_concurrent": 16,
    "timeout_seconds": 30,
}
ALL_FORMATS = ("webp", "avif", "jpeg", "png")
ALL_FITS = ("contain", "cover", "fill")
SOURCES = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
    "image/gif": "GIF",
    "image/avif": "AVIF",
    "image/bmp": "BMP",
}
MIME = {"webp": "image/webp", "avif": "image/avif", "jpeg": "image/jpeg", "png": "image/png"}
PARAMS = {
    "width": "width",
    "w": "width",
    "height": "height",
    "h": "height",
    "fit": "fit",
    "quality": "quality",
    "q": "quality",
    "format": "format",
    "f": "format",
    "rotate": "rotate",
    "crop": "crop",
}


class TransformError(Exception):
    """A transformation that was asked for wrongly or is not allowed. Carries the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def settings(raw: Any) -> dict[str, Any]:
    """A bucket's transformation settings with defaults filled in and every bound clamped to the platform's ceilings."""
    merged = {**DEFAULTS, **(raw if isinstance(raw, dict) else {})}
    for name, ceiling in CEILINGS.items():
        try:
            value = int(merged[name])
        except (TypeError, ValueError):
            value = DEFAULTS[name]
        merged[name] = max(1, min(value, ceiling))
    merged["enabled"] = bool(merged["enabled"])
    merged["auto_format"] = bool(merged["auto_format"])
    merged["quality"] = max(1, min(int(merged["quality"] or 80), 100))
    merged["formats"] = [f for f in merged["formats"] if f in ALL_FORMATS] or ["webp"]
    merged["fits"] = [f for f in merged["fits"] if f in ALL_FITS] or ["contain"]
    return merged


def validate_settings(raw: Any) -> dict[str, Any]:
    """Settings as saved from Studio or the API: unknown names and out-of-range numbers are refused, not silently clamped."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("transforms must be an object")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown transform setting: {', '.join(sorted(unknown))}")
    for name, ceiling in CEILINGS.items():
        if name in raw and not (isinstance(raw[name], int) and 1 <= raw[name] <= ceiling):
            raise ValueError(f"{name} must be a whole number from 1 to {ceiling}")
    if "quality" in raw and not (isinstance(raw["quality"], int) and 1 <= raw["quality"] <= 100):
        raise ValueError("quality must be from 1 to 100")
    for name, allowed in (("formats", ALL_FORMATS), ("fits", ALL_FITS)):
        if name in raw and (
            not isinstance(raw[name], list) or not raw[name] or set(raw[name]) - set(allowed)
        ):
            raise ValueError(f"{name} must be a non-empty list of: {', '.join(allowed)}")
    for name in ("enabled", "auto_format"):
        if name in raw and not isinstance(raw[name], bool):
            raise ValueError(f"{name} must be true or false")
    return dict(raw)


@dataclass(frozen=True, slots=True)
class Options:
    width: int = 0
    height: int = 0
    fit: str = "contain"
    quality: int = 0
    format: str = ""  # "" keeps the source format; "auto" negotiates with Accept
    rotate: int = 0
    crop: tuple[int, int, int, int] | None = None

    def cache_part(self, output: str) -> str:
        return (
            f"{self.width}x{self.height}:{self.fit}:q{self.quality}:{output}:r{self.rotate}:"
            f"{','.join(map(str, self.crop)) if self.crop else '-'}"
        )


def requested(query: Any) -> bool:
    """Whether the query asks for any transformation."""
    return any(name in query for name in PARAMS)


def parse(query: Any, cfg: dict[str, Any]) -> Options:
    """Read and validate transformation parameters against the bucket's settings."""
    if not cfg["enabled"]:
        raise TransformError(400, "image transformations are not enabled for this bucket")
    values: dict[str, str] = {}
    for name, target in PARAMS.items():
        if name in query and str(query.get(name)) != "":
            values.setdefault(target, str(query.get(name)))

    def number(name: str, low: int, high: int) -> int:
        try:
            value = int(values[name])
        except ValueError:
            raise TransformError(400, f"{name} must be a whole number") from None
        if not low <= value <= high:
            raise TransformError(400, f"{name} must be from {low} to {high}")
        return value

    width = number("width", 1, cfg["max_width"]) if "width" in values else 0
    height = number("height", 1, cfg["max_height"]) if "height" in values else 0
    fit = values.get("fit", "contain")
    if fit not in cfg["fits"]:
        raise TransformError(400, f"fit must be one of: {', '.join(cfg['fits'])}")
    quality = number("quality", 1, 100) if "quality" in values else 0
    fmt = values.get("format", "").lower()
    if fmt == "jpg":
        fmt = "jpeg"
    if fmt == "auto":
        if not cfg["auto_format"]:
            raise TransformError(400, "automatic format negotiation is not enabled")
    elif fmt and fmt not in cfg["formats"]:
        raise TransformError(400, f"format must be one of: auto, {', '.join(cfg['formats'])}")
    rotate = number("rotate", -360, 360) if "rotate" in values else 0
    crop = None
    if "crop" in values:
        try:
            parts = tuple(int(p) for p in values["crop"].split(","))
        except ValueError:
            parts = ()
        if len(parts) != 4 or parts[0] < 0 or parts[1] < 0 or parts[2] < 1 or parts[3] < 1:
            raise TransformError(400, "crop must be x,y,width,height")
        crop = parts  # type: ignore[assignment]
    return Options(width, height, fit, quality, fmt, rotate % 360, crop)


def negotiate(accept: str, cfg: dict[str, Any], source_type: str) -> str:
    """The best output format the caller's ``Accept`` header allows, among those the bucket permits."""
    accept = (accept or "").lower()
    for candidate in ("avif", "webp"):
        if candidate in cfg["formats"] and f"image/{candidate}" in accept:
            return candidate
    own = {"image/jpeg": "jpeg", "image/png": "png"}.get(source_type, "")
    if own in cfg["formats"]:
        return own
    return "jpeg" if "jpeg" in cfg["formats"] else cfg["formats"][0]


def render(data: bytes, options: Options, output: str, cfg: dict[str, Any]) -> bytes:
    """Decode, transform and encode. Blocking: call it in a thread."""
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > cfg["max_input_pixels"]:
            raise TransformError(413, "the image has too many pixels to transform")
        image = ImageOps.exif_transpose(image)
        image.load()
    except TransformError:
        raise
    except Exception as exc:
        raise TransformError(422, "the object could not be decoded as an image") from exc

    if options.crop:
        x, y, w, h = options.crop
        if x >= image.width or y >= image.height:
            raise TransformError(400, "the crop is outside the image")
        image = image.crop((x, y, min(x + w, image.width), min(y + h, image.height)))
    if options.rotate:
        image = image.rotate(-options.rotate, expand=True)
    if options.width or options.height:
        w = options.width or max(1, round(image.width * options.height / image.height))
        h = options.height or max(1, round(image.height * options.width / image.width))
        w, h = min(w, cfg["max_width"]), min(h, cfg["max_height"])
        if options.fit == "cover":
            image = ImageOps.fit(image, (w, h), Image.Resampling.LANCZOS)
        elif options.fit == "fill":
            image = image.resize((w, h), Image.Resampling.LANCZOS)
        else:  # contain: fit inside the box, keeping the ratio, never enlarging
            image = ImageOps.contain(image, (w, h), Image.Resampling.LANCZOS)
            if image.width > cfg["max_width"] or image.height > cfg["max_height"]:
                image = ImageOps.contain(
                    image, (cfg["max_width"], cfg["max_height"]), Image.Resampling.LANCZOS
                )
    elif image.width > cfg["max_width"] or image.height > cfg["max_height"]:
        image = ImageOps.contain(
            image, (cfg["max_width"], cfg["max_height"]), Image.Resampling.LANCZOS
        )

    if output == "jpeg" and image.mode not in ("RGB", "L"):
        flat = Image.new("RGB", image.size, "white")
        rgba = image.convert("RGBA")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    elif image.mode == "P":
        image = image.convert("RGBA")
    out = io.BytesIO()
    quality = options.quality or cfg["quality"]
    save: dict[str, Any] = {"optimize": True} if output in ("jpeg", "png") else {}
    if output != "png":
        save["quality"] = quality
    try:
        image.save(out, format=output.upper(), **save)
    except Exception as exc:
        raise TransformError(422, f"the image could not be encoded as {output}") from exc
    result = out.getvalue()
    if len(result) > cfg["max_output_bytes"]:
        raise TransformError(413, "the transformed image is larger than this bucket allows")
    return result


class Variants:
    """A bounded in-memory cache of rendered variants, per process.

    Keys carry the environment, bucket, key, object version (ETag) and the exact options, so replacing an object or asking for a
    different size never reuses an entry. The caller checks the object's read policy before every lookup: this cache holds bytes,
    never decisions.
    """

    def __init__(self, max_bytes: int = 64 * 1024 * 1024, max_item: int = 8 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self.max_item = max_item
        self._items: OrderedDict[tuple, bytes] = OrderedDict()
        self._size = 0
        self.hits = 0
        self.misses = 0
        self._gates: dict[tuple[str, str], tuple[int, asyncio.Semaphore]] = {}

    def get(self, key: tuple) -> bytes | None:
        value = self._items.get(key)
        if value is None:
            self.misses += 1
            return None
        self._items.move_to_end(key)
        self.hits += 1
        return value

    def put(self, key: tuple, value: bytes) -> None:
        if len(value) > self.max_item:
            return
        if key in self._items:
            self._size -= len(self._items.pop(key))
        self._items[key] = value
        self._size += len(value)
        while self._size > self.max_bytes and self._items:
            _, evicted = self._items.popitem(last=False)
            self._size -= len(evicted)

    def purge(self, env: str, bucket: str, key: str | None = None) -> None:
        """Drop variants of one object (or of a whole bucket) when it is replaced or deleted."""
        for item in [
            k
            for k in self._items
            if k[0] == env and k[1] == bucket and (key is None or k[2] == key)
        ]:
            self._size -= len(self._items.pop(item))

    def gate(self, env: str, bucket: str, limit: int) -> asyncio.Semaphore:
        held = self._gates.get((env, bucket))
        if held is None or held[0] != limit:
            held = self._gates[(env, bucket)] = (limit, asyncio.Semaphore(limit))
        return held[1]

    async def produce(
        self,
        scope: tuple[str, str, str],
        version: str,
        source: Any,
        options: Options,
        output: str,
        cfg: dict[str, Any],
    ) -> bytes:
        """The variant: from the cache, or rendered once under the bucket's concurrency and time limits.

        Args:
            scope: ``(env, bucket, key)``.
            version: The object's ETag, so a replaced object never serves the old variant.
            source: An async callable returning the original's bytes (bounded by ``max_source_bytes``).
        """
        key = (*scope, version, options.cache_part(output))
        cached = self.get(key)
        if cached is not None:
            return cached
        gate = self.gate(scope[0], scope[1], cfg["max_concurrent"])
        try:
            await asyncio.wait_for(gate.acquire(), cfg["timeout_seconds"])
        except TimeoutError:
            raise TransformError(
                503, "too many image transformations are running; try again"
            ) from None
        try:
            data = await source()
            result = await asyncio.wait_for(
                asyncio.to_thread(render, data, options, output, cfg), cfg["timeout_seconds"]
            )
        except TimeoutError:
            raise TransformError(503, "the transformation took too long") from None
        finally:
            gate.release()
        self.put(key, result)
        return result
