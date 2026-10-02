"""Uploading images, and serving them back.

Pawabase routes carry JSON, so an image is sent base64-encoded as ``{"file": "<base64 or data URL>"}`` (the 12 MB cap applies to
the *decoded* bytes). The endpoint does the least it can: sniff the bytes (never trust the declared type), write the original to
the public ``media`` bucket, create the row and announce ``image.uploaded``; resampling into sizes, encoding WebP and building the
placeholder is a queued job (``image.process``), so a merchant dragging in eight photos gets eight instant responses.

Serving is the storage API: ``GET /storage/v1/object/media/<key>`` (public bucket; ``Storage.getPublicUrl`` in the SDK), or a CDN when
``MEDIA_BASE_URL`` is set. Content-addressed keys never change contents, so they are safe to cache for a year.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import media, q
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import ApiError, bad_request, not_found
from sell4me_kit.events import emit
from sell4me_kit.services import storefront as storefront_service


def _decode(c: Ctx, raw: Any, limit: int) -> bytes:
    if not isinstance(raw, str) or not raw:
        raise bad_request("No file was sent.", "no_file")
    encoded = raw.split(",", 1)[1] if raw.startswith("data:") and "," in raw else raw
    try:
        data = base64.b64decode(encoded, validate=False)
    except (ValueError, binascii.Error):
        raise bad_request("The file is not valid base64.", "bad_file") from None
    if not data:
        raise bad_request("That file was empty.", "empty_file")
    if len(data) > limit:
        raise ApiError(413, "too_large", f"That file is {len(data) // 1024 // 1024}MB. The limit is {limit // 1024 // 1024}MB.")
    if media.sniff_image(data) not in media.ACCEPTED_IMAGE_TYPES:
        raise ApiError(415, "unsupported_type", "That is not an image we accept. Use JPEG, PNG, WebP or AVIF.")
    return data


_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/avif": "avif", "image/gif": "gif"}


@endpoint("images.upload", "POST", "/dash/{store}/products/{product_id}/images", area="catalog", permission="products.update",
          summary="Upload a product image (base64); derivatives are made by a queued job",
          fields=[{"name": "file", "type": "text", "required": True}, {"name": "alt", "type": "string"}], original="POST /products/{id}/images")
async def image_upload(c: Ctx):
    await c.dashboard("products.update")
    db, store = await c.db(), c.store
    product = await q.first(db, "products", {"id": c.int_arg("product_id", required=True), "store_id": store.pk})
    if product is None:
        raise not_found("That product")
    settings = await c.settings()
    data = _decode(c, c.input.get("file"), settings.max_upload_bytes)
    kind = media.sniff_image(data)
    # Content-addressed: re-uploading the same file lands in the same directory, and every derivative sits beside its original.
    key = media.object_key(store_id=store.pk, kind="products", owner_id=product.pk, name=f"original.{_EXT[kind]}", digest=hashlib.sha256(data).hexdigest()[:16])
    try:
        await media.write(c, key, data, content_type=kind)
    except Exception as error:  # noqa: BLE001
        raise bad_request(str(error), "storage_refused") from error
    highest = await q.first(db, "product_images", {"product_id": product.pk}, order="position DESC")
    image = await q.insert(db, "product_images", {"product_id": product.pk, "url": media.media_url(settings, key), "storage_key": key,
                                                  "alt": str(c.input.get("alt") or product.title)[:255], "position": (highest.position + 1) if highest else 0, "variants": {}})
    await emit(c, "image.uploaded", store=store, product=product, image=image)
    return {"id": image.pk, "url": image.url, "alt": image.alt, "position": image.position, "processing": True}


@endpoint("images.delete", "DELETE", "/dash/{store}/images/{image_id}", area="catalog", permission="products.update",
          summary="Remove an image and everything derived from it", original="POST /images/{id}/delete")
async def image_delete(c: Ctx):
    """One prefix delete rather than a list of keys to keep in step: that is why the derivatives were written beside the original."""
    await c.dashboard("products.update")
    db = await c.db()
    image = await db.one("SELECT i.* FROM product_images i JOIN products p ON p.id = i.product_id WHERE i.id = ? AND p.store_id = ? AND i.deleted_at IS NULL",
                         [c.int_arg("image_id", required=True), c.store.pk])
    if image is None:
        raise not_found("That image")
    image = q.Row(image)
    if image.storage_key:
        await media.delete_image(c, image.storage_key)  # the row goes either way: a leftover object is a bill, a row pointing at nothing is a broken page
    await db.delete("product_images", image.pk)
    return {"deleted": True}


@endpoint("images.reorder", "POST", "/dash/{store}/products/{product_id}/images/order", area="catalog", permission="products.update",
          summary="Set the display order from a list of image ids", fields=[{"name": "ids", "type": "json", "required": True}], original="POST /products/{id}/images/order")
async def image_reorder(c: Ctx):
    await c.dashboard("products.update")
    db = await c.db()
    product = await q.first(db, "products", {"id": c.int_arg("product_id", required=True), "store_id": c.store.pk})
    if product is None:
        raise not_found("That product")
    ids = c.input.get("ids")
    if not isinstance(ids, list):
        raise bad_request("Send an `ids` array.")
    for position, image_id in enumerate(ids):
        await q.update_where(db, "product_images", {"id": int(image_id), "product_id": product.pk}, {"position": position})
    return {"ok": True}


@endpoint("images.status", "GET", "/dash/{store}/products/{product_id}/images/status", area="catalog", permission="products.read",
          summary="Which images have finished being resized (the uploader polls this)", original="GET /products/{id}/images/status")
async def image_status(c: Ctx):
    await c.dashboard("products.read")
    db = await c.db()
    product = await q.first(db, "products", {"id": c.int_arg("product_id", required=True), "store_id": c.store.pk})
    if product is None:
        raise not_found("That product")
    return {"images": [{"id": i.pk, "url": i.url, "alt": i.alt, "placeholder": i.placeholder, "dominant_color": i.dominant_color, "processing": i.processed_at is None}
                       for i in await q.find(db, "product_images", {"product_id": product.pk}, order="position, id")]}


@endpoint("storefront.upload_logo", "POST", "/dash/{store}/storefront/logo", area="storefront", permission="storefront.update",
          summary="Upload the shop's logo (the same sniffing, size cap and storage as a product photograph)",
          fields=[{"name": "file", "type": "text", "required": True}], original="POST /storefront/logo")
async def upload_logo(c: Ctx):
    await c.dashboard("storefront.update")
    db, store = await c.db(), c.store
    settings = await c.settings()
    data = _decode(c, c.input.get("file"), settings.max_upload_bytes)
    kind = media.sniff_image(data)
    key = media.object_key(store_id=store.pk, kind="brand", owner_id=store.pk, name=f"logo.{_EXT[kind]}", digest=hashlib.sha256(data).hexdigest()[:16])
    try:
        await media.write(c, key, data, content_type=kind)
    except Exception as error:  # noqa: BLE001
        raise bad_request(str(error), "storage_refused") from error
    url = media.media_url(settings, key)
    theme = await storefront_service.ensure_theme(db, store)
    await q.update(db, "themes", theme.pk, {"logo_url": url})
    return {"url": url}
