"""The errors the API answers with.

Every failure a caller can act on is an :class:`ApiError`: an HTTP status, a stable
machine ``code`` and a ``message`` written to be shown. It is raised as Sillo's
``HTTPException`` carrying ``{"error", "message", "details"}`` as its detail, which the
platform sends as the response body unchanged, the same shape functions and flows use.
"""

from __future__ import annotations

from typing import Any

from sillo.exceptions import HTTPException


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        body: dict[str, Any] = {"error": code, "message": message}
        if details is not None:
            body["details"] = details
        super().__init__(status_code=status, detail=body)
        self.code = code
        self.message = message


def bad_request(message: str, code: str = "bad_request", details: Any = None) -> ApiError:
    return ApiError(400, code, message, details)


def unauthenticated(message: str = "Sign in first.") -> ApiError:
    return ApiError(401, "unauthenticated", message)


def forbidden(message: str = "You may not do that.", code: str = "forbidden") -> ApiError:
    return ApiError(403, code, message)


def missing_permission(permission: str) -> ApiError:
    return ApiError(403, "missing_permission", f"Missing permission: {permission}", {"permission": permission})


def not_found(what: str = "That") -> ApiError:
    return ApiError(404, "not_found", f"{what} was not found.")


def conflict(message: str, code: str = "conflict") -> ApiError:
    return ApiError(409, code, message)


def unprocessable(message: str, code: str = "invalid", details: Any = None) -> ApiError:
    return ApiError(422, code, message, details)
