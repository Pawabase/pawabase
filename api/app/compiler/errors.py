"""OpenAPI response schemas for the errors every compiled route can raise.

The data-plane never wraps an error in an envelope like ``{"detail": ...}``:
the JSON body is exactly the exception's ``detail`` (see
:class:`sillo.exceptions.HTTPException` and the framework's error handler).
So these mirror that precisely — a bare string for a message, a bare array
of Pydantic-shaped items for a 422 — rather than the conventional-but-wrong
"error object" shape most OpenAPI docs assume.

Used as ``responses=responses(UNAUTHENTICATED, FORBIDDEN, NOT_FOUND)`` when
registering a route, so the generated docs list every status code a caller
can actually receive, not just the happy path.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, RootModel


class ErrorDetail(RootModel[str]):
    """A plain-text error message — the exact response body for 401, 403,
    404, 409 and most other non-validation errors (`PermissionDenied`,
    `AuthenticationFailed`, and every ``HTTPException(status_code, detail="...")``
    raised in the compiler)."""


class ValidationErrorItem(BaseModel):
    """One field's validation failure, Pydantic's own error shape."""

    type: str = Field(description="The failure's kind, e.g. 'missing' or 'string_type'.")
    loc: list[str | int] = Field(description="Path to the offending field, e.g. ['email'].")
    msg: str = Field(description="Human-readable explanation.")
    input: object | None = Field(default=None, description="The value that failed validation.")
    url: str | None = Field(default=None, description="Pydantic's docs link for this error kind.")


class ValidationErrors(RootModel[list[ValidationErrorItem]]):
    """The exact response body for 422: a bare array, one entry per invalid field."""


#: Reusable per-status response groups. Combine with :func:`responses`.
UNAUTHENTICATED = {401: ErrorDetail}
FORBIDDEN = {403: ErrorDetail}
NOT_FOUND = {404: ErrorDetail}
CONFLICT = {409: ErrorDetail}
UNPROCESSABLE = {422: ValidationErrors}


def responses(*groups: dict[int, type]) -> dict[int, type]:
    """Merge response groups into the ``responses=`` mapping a route wants.

    ``responses(UNAUTHENTICATED, FORBIDDEN, NOT_FOUND)`` — order doesn't
    matter, later groups win on a status-code clash (there shouldn't be one).
    """
    merged: dict[int, type] = {}
    for group in groups:
        merged.update(group)
    return merged
