"""What an endpoint handler works with.

Pawabase hands a function a ``FunctionContext`` (the caller, the request, the runtime).
:class:`Ctx` wraps it with what every endpoint here needs: the database, the store the
request is for, the member acting in it, and the one question the original application
asked on every route, "may this person do this here?".

The store comes from the path (``/dash/{store}/…``) and is never trusted: every
dashboard call looks up the caller's membership of *that* store and refuses without one,
which is the same guarantee the original gave by resolving the store from the session.
"""

from __future__ import annotations

import datetime as dt
import logging
from contextvars import ContextVar
from typing import Any

from . import q
from . import settings
from .errors import ApiError, bad_request, forbidden, missing_permission, not_found, unauthenticated
from .services import preview
from .perms import member_can, permissions_of


log = logging.getLogger("sell4me")
_CURRENT: ContextVar["Ctx | None"] = ContextVar("sell4me_ctx", default=None)


def current_ctx() -> "Ctx | None":
    """The request's context, for code that sits behind an interface that cannot carry one (a payment provider)."""
    return _CURRENT.get()


class Ctx:
    def __init__(self, ctx: Any) -> None:
        self.raw = ctx
        _CURRENT.set(self)
        self.runtime = ctx.runtime
        self.auth: dict[str, Any] = dict(ctx.auth or {})
        request = ctx.request or {}
        self.query: dict[str, Any] = dict(request.get("query") or {})
        self.headers: dict[str, str] = dict(request.get("headers") or {})
        self.client_ip: str | None = request.get("client_ip")
        self.params: dict[str, Any] = dict(request.get("params") or {})
        data = ctx.input
        # Pawabase validates a route's body against its declared fields and fills every field the client did not send with ``None``. The original read form
        # posts, where an absent field is absent, so null is dropped here: ``null`` and "not sent" mean the same thing, and a field is cleared by sending ``""``.
        self.input: dict[str, Any] = {k: v for k, v in data.items() if v is not None} if isinstance(data, dict) else {}
        self.trigger: str = ctx.trigger
        self.store: q.Row | None = None
        self.member: q.Row | None = None
        self.previewing = False
        self._db: Any = None

    # ── who ──────────────────────────────────────────────────────────────

    @property
    def user_id(self) -> str | None:
        return self.auth.get("user_id") if self.auth.get("authenticated") else None

    @property
    def email(self) -> str | None:
        return self.auth.get("email")

    def require_user(self) -> str:
        if not self.user_id:
            raise unauthenticated()
        return self.user_id

    @property
    def is_service(self) -> bool:
        return self.trigger in ("job", "schedule", "event", "flow") or bool(self.auth.get("is_service"))

    # ── data ─────────────────────────────────────────────────────────────

    async def db(self) -> Any:
        if self._db is None:
            self._db = await self.runtime.db()
        return self._db

    def scope(self, db: Any = None) -> Any:
        """``async with c.scope(db) as db``: join the caller's transaction, or open one."""
        import contextlib

        if db is not None:
            @contextlib.asynccontextmanager
            async def joined():
                yield db

            return joined()
        return self.runtime.transaction()

    def tx(self) -> Any:
        """``async with c.tx() as db:`` commits on success and rolls back on any error."""
        return self.runtime.transaction()

    # ── which store, and may they ────────────────────────────────────────

    async def dashboard(self, *permissions: str) -> "Ctx":
        """Resolve the store in ``{store}`` and the caller's membership, and require every permission.

        Raises 401 when signed out, 404 when the store does not exist *or* the caller is not
        in it (so a store's existence is not disclosed), 403 naming the first missing
        permission.
        """
        user_id = self.require_user()
        slug = self.input.get("store") or self.params.get("store")
        if not slug:
            raise bad_request("Name the store in the path.", "store_required")
        db = await self.db()
        store = await q.first(db, "stores", {"slug": str(slug)})
        member = (
            await q.first(db, "store_members", {"store_id": store.pk, "user_id": user_id, "status": "active"})
            if store
            else None
        )
        if store is None or member is None:
            raise not_found("That store")
        self.store, self.member = store, member
        for permission in permissions:
            if not member_can(member, permission):
                raise missing_permission(permission)
        return self

    def can(self, permission: str) -> bool:
        return bool(self.member) and member_can(self.member, permission)

    def permissions(self) -> list[str]:
        return sorted(permissions_of(self.member)) if self.member else []

    async def shop(self, *, during_maintenance: bool = False) -> "Ctx":
        """Resolve the storefront's store from ``{store}``.

        A store that is not live is simply not found, unless the request carries a signed preview token for *this* store (the shop is a
        different hostname from the dashboard, so no session can prove membership; the token is what makes "View shop" work before launch).
        A store in maintenance answers 503 with the holding page's content, except for the pages that must keep working regardless (an order's
        status link and the payment return: a shopper who has already paid is never locked out of their own receipt). A previewing merchant
        can ask for the holding page itself with ``holding=1``.
        """
        slug = self.input.get("store") or self.params.get("store")
        if not slug:
            raise bad_request("Name the store in the path.", "store_required")
        db = await self.db()
        store = await q.first(db, "stores", {"slug": str(slug)})
        if store is None:
            raise not_found("That shop")
        settings = await self.settings()
        self.previewing = preview.verify(self.query.get("preview"), settings.secret_key) == store.pk
        if store.status != "active" and not self.previewing:
            raise not_found("That shop")
        holding = (self.previewing and self.query.get("holding") == "1") or (store.maintenance_enabled and not during_maintenance and not self.previewing)
        if holding:
            raise ApiError(503, "maintenance", store.maintenance_title or "We'll be right back", {
                "title": store.maintenance_title or "We'll be right back", "message": store.maintenance_message or "", "ends_at": store.maintenance_ends_at})
        self.store = store
        return self

    @property
    def cart_token(self) -> str | None:
        """The shopper's basket token, held by the client (there is no cookie session): header ``x-cart-token``, or ``cart_token`` in the body or query."""
        return self.headers.get("x-cart-token") or self.input.get("cart_token") or self.query.get("cart_token") or None

    # ── request helpers ──────────────────────────────────────────────────

    def page_params(self, default: int = 25, maximum: int = 100) -> tuple[int, int]:
        try:
            page = max(1, int(self.query.get("page", 1)))
            per_page = max(1, min(int(self.query.get("per_page", default)), maximum))
        except ValueError as exc:
            raise bad_request("page and per_page must be integers.") from exc
        return page, per_page

    def arg(self, name: str, default: Any = None) -> Any:
        """A value from the body, the path, or the query string, in that order."""
        if name in self.input:
            return self.input[name]
        return self.query.get(name, default)

    def need(self, name: str) -> Any:
        value = self.arg(name)
        if value in (None, ""):
            raise bad_request(f"{name} is required.", "missing_field", {"field": name})
        return value

    def int_arg(self, name: str, default: int | None = None, *, required: bool = False) -> int | None:
        value = self.arg(name)
        if value in (None, ""):
            if required:
                raise bad_request(f"{name} is required.", "missing_field", {"field": name})
            return default
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise bad_request(f"{name} must be a whole number.", "invalid_field", {"field": name}) from exc

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    async def emit(self, name: str, payload: Any) -> None:
        try:
            await self.runtime.emit(name, payload)
        except Exception:  # an announcement must never fail the request that caused it
            pass

    async def dispatch(self, function: str, payload: dict[str, Any], *, delay: int = 0, queue: str | None = None) -> None:
        """Queue a job function. Never raises: queuing is not worth failing the request."""
        try:
            await self.runtime.dispatch_function(function, payload, delay=delay, queue=queue)
        except Exception:
            log.exception("could not queue %s", function)

    async def settings(self) -> Any:
        if getattr(self, "_settings", None) is None:
            self._settings = await settings.load(self.runtime)
        return self._settings

    def forbid(self, message: str = "You may not do that.") -> None:
        raise forbidden(message)
