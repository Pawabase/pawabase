"""Field types the platform models share."""

from __future__ import annotations

from typing import Any

from tortoise import fields
from tortoise.exceptions import FieldError


class AnyJSONField(fields.JSONField):
    """A JSON column that also stores bare strings.

    Tortoise's ``JSONField`` treats a ``str`` as already-encoded JSON, so a
    policy reference such as ``"authenticated"`` is rejected as invalid JSON.
    Definitions legitimately hold any JSON value (a policy name, an inline
    condition, ``true``), so strings are encoded like everything else.
    """

    def to_db_value(self, value: Any, instance: Any) -> Any:
        if isinstance(value, str):
            return self.encoder(value)
        return super().to_db_value(value, instance)

    def to_python_value(self, value: Any) -> Any:
        try:
            return super().to_python_value(value)
        except FieldError:
            return value  # already a Python string
