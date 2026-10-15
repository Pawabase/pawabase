"""Serving a stored object to a browser or an app: types, disposition, ranges, validators, compression, transformations.

Everything that decides *who* may read an object happens before this module is called (the bucket's policy, or a share link's grant).
This module decides only *how* the bytes go out.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import zlib
from collections.abc import AsyncIterator, Mapping
from email.utils import formatdate
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sillo import stream
from sillo.storage.base import FileInfo

from pawabase_server.infra.storage import transform

CHUNK = 64 * 1024

#: Types a browser may render in place. Anything that can run script in our origin (HTML, SVG, XML, JavaScript) is deliberately
#: absent: those are always delivered as attachments.
INLINE_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/avif",
        "image/bmp",
        "image/x-icon",
        "image/vnd.microsoft.icon",
        "video/mp4",
        "video/webm",
        "video/ogg",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "audio/mp4",
        "audio/aac",
        "audio/flac",
        "application/pdf",
        "text/plain",
        "text/csv",
    }
)
COMPRESSIBLE = re.compile(
    r"^(text/(plain|csv|css|markdown)|application/(json|xml|x-ndjson|javascript)|image/svg\+xml)$"
)
GUARDS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cross-origin-resource-policy": "cross-origin",
}
RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangeNotSatisfiable(Exception):
    pass


def parse_range(value: str, size: int) -> tuple[int, int] | None:
    """The single byte range a ``Range`` header asks for, inclusive, or ``None`` to send the whole object.

    Multiple ranges and unknown units are ignored (the whole object is a valid answer); a range that starts past the end is
    unsatisfiable.
    """
    match = RANGE.match(value.strip())
    if not match or "," in value:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:  # the last N bytes
        count = int(last)
        if count == 0:
            raise RangeNotSatisfiable
        return max(0, size - count), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise RangeNotSatisfiable
    return start, end


def disposition(
    content_type: str, key: str, *, download: str | None, allow_inline: bool = True
) -> str:
    """``Content-Disposition`` for an object: inline for safe media, an attachment for everything else or when a download was asked for."""
    name = download if download and download not in ("1", "true") else key.rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f"\\/]', "_", name)[:200] or "download"
    inline = allow_inline and content_type in INLINE_TYPES and not download
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_")
    return (
        f'{"inline" if inline else "attachment"}; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(name, safe='')}"
    )


def security_headers(content_type: str) -> dict[str, str]:
    headers = dict(GUARDS)
    if content_type != "application/pdf":
        headers["content-security-policy"] = "default-src 'none'; sandbox"
    return headers


async def _file_range(path: Path, start: int, end: int) -> AsyncIterator[bytes]:
    remaining = end - start + 1

    def open_at():
        handle = path.open("rb")
        handle.seek(start)
        return handle

    handle = await asyncio.to_thread(open_at)
    try:
        while remaining > 0:
            chunk = await asyncio.to_thread(handle.read, min(CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        await asyncio.to_thread(handle.close)


async def read_range(driver: Any, key: str, start: int, end: int) -> AsyncIterator[bytes]:
    """Bytes ``start``..``end`` of an object. Drivers that can seek do; the rest skip, still without holding the object in memory."""
    if hasattr(driver, "read_range"):
        async for chunk in driver.read_range(key, start, end):
            yield chunk
        return
    local = getattr(driver, "_path", None)
    if local is not None:
        async for chunk in _file_range(local(key), start, end):
            yield chunk
        return
    position = 0
    async for chunk in driver.read(key):
        upper = position + len(chunk)
        if upper > start and position <= end:
            yield chunk[max(0, start - position) : max(0, end - position + 1)]
        position = upper
        if position > end:
            break


async def _gzip(source: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    packer = zlib.compressobj(6, zlib.DEFLATED, 31)
    async for chunk in source:
        packed = packer.compress(chunk)
        if packed:
            yield packed
    yield packer.flush()


async def _empty() -> AsyncIterator[bytes]:
    return
    yield b""  # pragma: no cover


async def _bytes(data: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(data), CHUNK):
        yield data[offset : offset + CHUNK]


def respond(
    body: AsyncIterator[bytes],
    *,
    status_code: int = 200,
    content_type: str,
    headers: Mapping[str, str],
):
    """A streamed response that still states its length.

    Sillo's streaming response removes ``Content-Length`` when it is built (it cannot know a generator's size), so a caller
    that does know it - a stored object, a byte range - sets it again afterwards. Players need it to size a seek bar.
    """
    headers = dict(headers)
    length = headers.pop("content-length", None)
    response = stream(body, status_code=status_code, content_type=content_type, headers=headers)
    if length is not None:
        response.headers["content-length"] = length
    return response


def error(status: int, message: str, headers: Mapping[str, str] | None = None):
    return stream(
        _bytes(json.dumps({"detail": message}).encode()),
        status_code=status,
        content_type="application/json",
        headers={"cache-control": "no-store", **(headers or {})},
    )


def cache_control(*, public: bool, max_age: int) -> str:
    scope = "public" if public else "private"
    if max_age <= 0:
        return f"{scope}, max-age=0, must-revalidate"
    return f"{scope}, max-age={max_age}"


class Delivery:
    """Everything a delivery route needs, bound for one request after authorisation has happened.

    Args:
        held: The Sillo bucket, with the request's policy (or ``signed=True`` for a share link, whose grant is already checked).
        env: The environment name (part of every cache key).
        key: The object key.
        headers: The request headers (lower-cased names).
        query: The query parameters.
        variants: The process's transformation cache.
        transforms: The bucket's transformation settings, unmerged.
        public: Whether the response may be cached by shared caches (the object is readable without credentials).
        max_age: ``Cache-Control: max-age`` seconds.
        user, signed: Passed through to the bucket for the read.
        allow_inline: ``False`` forces an attachment (a download-only share link).
        scope: Names the authorisation that let this request in (``policy`` or ``share:<id>``); part of the variant cache key.
        open_access: Adds a wildcard CORS header, for capability URLs and public objects fetched cross-origin.
    """

    def __init__(
        self,
        held: Any,
        *,
        env: str,
        key: str,
        headers: Mapping[str, str],
        query: Mapping[str, Any],
        variants: transform.Variants,
        transforms: Any,
        public: bool,
        max_age: int,
        user: Any = None,
        signed: bool = False,
        allow_inline: bool = True,
        scope: str = "policy",
        open_access: bool = False,
    ) -> None:
        self.held = held
        self.env = env
        self.key = key
        self.headers = headers
        self.query = query
        self.variants = variants
        self.cfg = transform.settings(transforms)
        self.public = public
        self.max_age = max_age
        self.user = user
        self.signed = signed
        self.allow_inline = allow_inline
        self.scope = scope
        self.open_access = open_access

    async def response(self, info: FileInfo, *, head: bool = False):
        content_type = info.content_type or "application/octet-stream"
        etag = info.etag or f"{info.size}-{int(info.modified)}"
        download = self.query.get("download")
        base = {
            "accept-ranges": "bytes",
            "cache-control": cache_control(public=self.public, max_age=self.max_age),
            "last-modified": _http_date(info.modified),
            **({"access-control-allow-origin": "*"} if self.open_access else {}),
        }
        if transform.requested(self.query):
            return await self._transformed(info, etag, base, head)

        quoted = f'"{etag}"'
        compress = (
            bool(COMPRESSIBLE.match(content_type))
            and "gzip" in self.headers.get("accept-encoding", "").lower()
            and "range" not in self.headers
            and info.size >= 512
        )
        base["vary"] = "Accept-Encoding" if COMPRESSIBLE.match(content_type) else ""
        base = {k: v for k, v in base.items() if v}
        shared = {
            **base,
            "etag": f"W/{quoted}" if compress else quoted,
            "content-disposition": disposition(
                content_type, self.key, download=download, allow_inline=self.allow_inline
            ),
            **security_headers(content_type),
        }
        if _matches(self.headers.get("if-none-match"), etag):
            return respond(_empty(), status_code=304, content_type=content_type, headers=shared)

        span = None
        wanted = self.headers.get("range")
        if wanted and self._range_applies(etag, info):
            try:
                span = parse_range(wanted, info.size)
            except RangeNotSatisfiable:
                return respond(
                    _empty(),
                    status_code=416,
                    content_type=content_type,
                    headers={**shared, "content-range": f"bytes */{info.size}"},
                )
        if span is not None:
            start, end = span
            headers = {
                **shared,
                "content-range": f"bytes {start}-{end}/{info.size}",
                "content-length": str(end - start + 1),
            }
            body = _empty() if head else self._ranged(start, end)
            return respond(body, status_code=206, content_type=content_type, headers=headers)
        if compress:
            headers = {**shared, "content-encoding": "gzip"}
            body = _empty() if head else _gzip(self._whole())
        else:
            headers = {**shared, "content-length": str(info.size)}
            body = _empty() if head else self._whole()
        return respond(body, content_type=content_type, headers=headers)

    def _range_applies(self, etag: str, info: FileInfo) -> bool:
        condition = self.headers.get("if-range")
        return not condition or condition.strip().strip('"').removeprefix("W/").strip('"') == etag

    def _whole(self) -> AsyncIterator[bytes]:
        return self.held.get(self.key, user=self.user, signed=self.signed)

    async def _ranged(self, start: int, end: int) -> AsyncIterator[bytes]:
        async for chunk in read_range(self.held.driver, self.key, start, end):
            yield chunk

    async def _source(self) -> bytes:
        limit = self.cfg["max_source_bytes"]
        data = bytearray()
        async for chunk in self._whole():
            data.extend(chunk)
            if len(data) > limit:
                raise transform.TransformError(413, "the object is too large to transform")
        return bytes(data)

    async def _transformed(self, info: FileInfo, etag: str, base: dict[str, str], head: bool):
        source_type = info.content_type
        try:
            options = transform.parse(self.query, self.cfg)
            if source_type not in transform.SOURCES:
                raise transform.TransformError(415, "only raster images can be transformed")
            if info.size > self.cfg["max_source_bytes"]:
                raise transform.TransformError(413, "the object is too large to transform")
            if options.format == "auto":
                output = transform.negotiate(self.headers.get("accept", ""), self.cfg, source_type)
            elif options.format:
                output = options.format
            else:
                output = {
                    "image/png": "png",
                    "image/jpeg": "jpeg",
                    "image/webp": "webp",
                    "image/avif": "avif",
                }.get(source_type, "png")
                if output not in self.cfg["formats"]:
                    output = self.cfg["formats"][0]
            version = f"{etag}:{self.scope}"
            result = await self.variants.produce(
                (self.env, self.held.name, self.key),
                version,
                self._source,
                options,
                output,
                self.cfg,
            )
        except transform.TransformError as exc:
            return error(exc.status, exc.message)
        variant = f'"{etag}-{hashlib.sha1(options.cache_part(output).encode()).hexdigest()[:12]}-{output}"'
        headers = {
            **base,
            "vary": "Accept" if options.format == "auto" else "",
            "etag": variant,
            "content-length": str(len(result)),
            "content-disposition": disposition(
                transform.MIME[output],
                _renamed(self.key, output),
                download=self.query.get("download"),
                allow_inline=self.allow_inline,
            ),
            **security_headers(transform.MIME[output]),
        }
        headers = {k: v for k, v in headers.items() if v}
        if _matches(self.headers.get("if-none-match"), variant.strip('"')):
            return respond(
                _empty(), status_code=304, content_type=transform.MIME[output], headers=headers
            )
        return respond(
            _empty() if head else _bytes(result),
            content_type=transform.MIME[output],
            headers=headers,
        )


def _renamed(key: str, output: str) -> str:
    stem, _, _ = key.rpartition(".")
    return f"{stem or key}.{'jpg' if output == 'jpeg' else output}"


def _matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    wanted = {part.strip().removeprefix("W/").strip('"') for part in header.split(",")}
    return etag in wanted


def _http_date(timestamp: float) -> str:
    if not timestamp:
        return ""
    return formatdate(timestamp, usegmt=True)
