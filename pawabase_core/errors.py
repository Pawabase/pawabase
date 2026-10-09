"""How Pawabase reports what breaks.

A failure inside a request used to reach the log as the whole Python traceback: thirty frames of the web framework, its middleware, the ORM and
the database driver, with the one line that matters (the function that raised) somewhere in the middle. This keeps only that line.

* :mod:`pawabase_core.failures` describes an exception: what went wrong, where in *your* code, and what to try.
* :func:`install` registers the catch-all every service uses. A failure becomes one log line and one JSON answer carrying a request id, never a
  traceback. Operators (Studio, other services) and ``debug`` also get where it broke and the hint; everyone else gets the request id to quote.
* :class:`PawabaseError` is for failures a service chooses to report (a status, a stable code, a message, a hint).

``PAWABASE_TRACEBACKS=true`` logs the complete traceback too, for chasing a bug in Pawabase itself.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sillo import HttpContext, json

from pawabase_core.failures import MAX_MESSAGE, Failure, hint_for, locate, report, root_cause
from pawabase_core.telemetry import note

logger = logging.getLogger("pawabase.errors")


class PawabaseError(Exception):
    """A failure a service reports on purpose.

    Attributes:
        status: The HTTP status to answer with.
        code: A stable, machine-readable name (``function_error``), safe to branch on.
        message: What went wrong, for a person. Shown to the caller as it is.
        hint: What to try. Optional.
        details: Anything structured the caller can use (field errors, the limit that was hit).
    """

    status = 500
    code = "error"

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        hint: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status if status is not None else type(self).status
        self.code = code or type(self).code
        self.hint = hint
        self.details = details


class FunctionFailed(PawabaseError):
    """A function raised something it did not mean to report. The original error is its ``__cause__``."""

    status = 500
    code = "function_error"


def request_id_of(ctx: HttpContext) -> str | None:
    state = getattr(ctx, "state", None)
    return getattr(state, "request_id", None) or ctx.headers.get("x-request-id")


def _operator(ctx: HttpContext, debug: bool) -> bool:
    """Whether the caller may see where it broke: Studio and other services (not an end user of an application), or any caller in debug."""
    if debug:
        return True
    try:
        return getattr(ctx.user, "kind", None) == "service"
    except Exception:  # no authentication on this request (a failure before the middleware ran)
        return False


def _note(failure: Failure) -> None:
    """Give the request being recorded the short account of the failure in place of a traceback."""
    note("error", failure.summary[:MAX_MESSAGE])
    note("traceback", failure.trace())


def render(
    ctx: HttpContext, exc: BaseException, *, service: str, debug: bool, roots: tuple[str, ...] = ()
):
    """Log *exc* as one line and answer with the JSON error envelope."""
    request_id = request_id_of(ctx)
    expected = isinstance(exc, PawabaseError)
    failure = report(exc, roots=roots)
    status = exc.status if expected else 500
    code = exc.code if expected else "internal_error"
    log_line = f"{status} {ctx.method} {ctx.url.path} [{code}] {failure.line()}" + (
        f" request={request_id}" if request_id else ""
    )
    if status >= 500:
        logger.error(
            "%s: %s",
            service,
            log_line,
            exc_info=exc if os.environ.get("PAWABASE_TRACEBACKS") == "true" else None,
        )
        _note(failure)
    else:
        logger.info("%s: %s", service, log_line)

    body: dict[str, Any] = {
        "error": code,
        "message": exc.message if expected else "Something went wrong on our side.",
    }
    if request_id:
        body["request_id"] = request_id
    if expected and exc.details is not None:
        body["details"] = exc.details
    if expected and exc.hint:
        body["hint"] = exc.hint
    if _operator(ctx, debug) and status >= 500:
        body["failure"] = {
            "kind": failure.kind,
            "message": failure.message,
            "where": [str(place) for place in failure.where],
            **({"hint": failure.hint} if failure.hint else {}),
        }
    return json(body, status_code=status)


_original_format_exception = logging.Formatter.formatException


def compact_logging() -> None:
    """Make every log record that carries an exception print the short account (your frames and the error), not the whole traceback.

    Services log failures with ``logger.exception(...)`` in dozens of places; changing the formatter once covers them, and the web server's
    and the libraries' own loggers with them. ``PAWABASE_TRACEBACKS=true`` brings the full traceback back. Safe to call more than once.
    """

    def format_exception(self: logging.Formatter, exc_info: Any) -> str:
        if os.environ.get("PAWABASE_TRACEBACKS") == "true" or not exc_info or exc_info[1] is None:
            return _original_format_exception(self, exc_info)
        return report(exc_info[1], roots=_roots()).trace()

    logging.Formatter.formatException = format_exception  # type: ignore[method-assign]


def _roots() -> tuple[str, ...]:
    return tuple(
        path
        for path in (
            os.environ.get("PAWABASE_DEPLOYMENTS_PATH"),
            os.environ.get("PAWABASE_CODE_PATH"),
        )
        if path
    )


def install(app: Any, service: str, *, debug: bool = False) -> None:
    """Make *app* answer every failure the same way: one log line, one JSON body, no traceback.

    Registers a handler for :class:`PawabaseError` (its status and code), one for any other exception (a 500), and the same one as the
    server-error handler for what escapes the middleware.
    """
    roots = _roots()
    compact_logging()

    async def failed(ctx: HttpContext, exc: Exception):
        return render(ctx, exc, service=service, debug=debug, roots=roots)

    app.add_exception_handler(PawabaseError, failed)
    app.add_exception_handler(Exception, failed)
    app.server_error_handler = failed


__all__ = [
    "Failure",
    "FunctionFailed",
    "PawabaseError",
    "compact_logging",
    "hint_for",
    "install",
    "locate",
    "render",
    "report",
    "root_cause",
]
