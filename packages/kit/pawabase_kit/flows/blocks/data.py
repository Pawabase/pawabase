"""Data blocks: transformation, validation, JSON, text, maths, ids, hashing, time."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from ...schemas import validate_payload
from ...templating import render
from ...transformers import apply_transformer
from ..engine import FlowError
from ..registry import Block, BlockResult


class Transform(Block):
    """Applies a transformer (inline, stored, or Python) to a value."""

    key = "transform.map"
    title = "Transform"
    category = "data"
    config = [
        {"name": "value", "type": "json", "required": True},
        {
            "name": "transformer",
            "type": "json",
            "required": True,
            "description": "Definition or transformer name",
        },
    ]

    async def run(self, config, run):
        output = await apply_transformer(
            config.get("value"), config.get("transformer"), context=run.state
        )
        return BlockResult(output=output)


class Template(Block):
    """Builds a value from a template (strings, objects or lists)."""

    key = "transform.template"
    title = "Template"
    category = "data"
    raw_config = True
    config = [{"name": "template", "type": "json", "required": True}]

    async def run(self, config, run):
        return BlockResult(output=render(config.get("template"), run.state))


class Pick(Block):
    """Keeps only some keys of an object."""

    key = "transform.pick"
    title = "Pick fields"
    category = "data"
    config = [
        {"name": "value", "type": "json", "required": True},
        {"name": "fields", "type": "array", "items": {"type": "string"}, "required": True},
    ]

    async def run(self, config, run):
        value = config.get("value") or {}
        fields = config.get("fields") or []
        if isinstance(value, list):
            return BlockResult(
                output=[{k: item.get(k) for k in fields if k in item} for item in value]
            )
        return BlockResult(output={k: value.get(k) for k in fields if k in value})


class Validate(Block):
    """Validates a value against field definitions. Fails with 422 or follows ``invalid``."""

    key = "validate.schema"
    title = "Validate"
    category = "data"
    handles = ["next", "invalid"]
    config = [
        {"name": "value", "type": "json", "required": True},
        {"name": "fields", "type": "json", "required": True, "description": "Field definitions"},
        {"name": "on_invalid", "type": "string", "enum": ["fail", "branch"], "default": "fail"},
    ]

    async def run(self, config, run):
        try:
            cleaned = validate_payload(config.get("fields") or [], config.get("value") or {})
        except ValidationError as exc:
            errors = json.loads(exc.json(include_url=False))
            if config.get("on_invalid") == "branch":
                return BlockResult(output={"errors": errors}, handle="invalid")
            raise FlowError(
                "validation failed", status=422, code="invalid", details=errors
            ) from exc
        return BlockResult(output=cleaned)


class JsonParse(Block):
    """Parses a JSON string."""

    key = "json.parse"
    title = "Parse JSON"
    category = "data"
    config = [{"name": "text", "type": "string", "required": True}]

    async def run(self, config, run):
        try:
            return BlockResult(output=json.loads(config.get("text") or "null"))
        except ValueError as exc:
            raise FlowError(f"invalid JSON: {exc}", status=400, code="bad_json") from exc


class JsonStringify(Block):
    """Serialises a value as JSON text."""

    key = "json.stringify"
    title = "Stringify JSON"
    category = "data"
    config = [
        {"name": "value", "type": "json", "required": True},
        {"name": "indent", "type": "integer"},
    ]

    async def run(self, config, run):
        return BlockResult(
            output=json.dumps(config.get("value"), default=str, indent=config.get("indent"))
        )


class Calculate(Block):
    """Arithmetic on numbers: add, subtract, multiply, divide, round, min, max, sum."""

    key = "math.calculate"
    title = "Calculate"
    category = "data"
    config = [
        {
            "name": "operation",
            "type": "string",
            "enum": [
                "add",
                "subtract",
                "multiply",
                "divide",
                "round",
                "min",
                "max",
                "sum",
                "average",
            ],
            "required": True,
        },
        {"name": "values", "type": "json", "required": True, "description": "List of numbers"},
        {"name": "digits", "type": "integer", "default": 2},
    ]

    async def run(self, config, run):
        values = config.get("values")
        values = values if isinstance(values, list) else [values]
        try:
            numbers = [float(v) for v in values]
        except (TypeError, ValueError) as exc:
            raise FlowError("calculate needs numbers", code="not_a_number") from exc
        op = config.get("operation")
        if op in ("add", "sum"):
            result = sum(numbers)
        elif op == "subtract":
            result = numbers[0] - sum(numbers[1:])
        elif op == "multiply":
            result = 1.0
            for number in numbers:
                result *= number
        elif op == "divide":
            if any(n == 0 for n in numbers[1:]):
                raise FlowError("division by zero", code="division_by_zero")
            result = numbers[0]
            for number in numbers[1:]:
                result /= number
        elif op == "round":
            result = round(numbers[0], int(config.get("digits", 2)))
        elif op == "min":
            result = min(numbers)
        elif op == "max":
            result = max(numbers)
        elif op == "average":
            result = sum(numbers) / len(numbers) if numbers else 0.0
        else:
            raise FlowError(f"unknown operation {op!r}", code="bad_config")
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return BlockResult(output=result)


class GenerateId(Block):
    """Generates a UUID4 or a random URL-safe token."""

    key = "util.id"
    title = "Generate id"
    category = "data"
    config = [
        {"name": "kind", "type": "string", "enum": ["uuid", "token"], "default": "uuid"},
        {"name": "length", "type": "integer", "default": 32},
    ]

    async def run(self, config, run):
        if config.get("kind") == "token":
            from sillo.helpers.strings import random_token

            return BlockResult(output=random_token(int(config.get("length") or 32)))
        return BlockResult(output=str(uuid.uuid4()))


class Hash(Block):
    """Hashes text with SHA-256, or signs it with HMAC-SHA256 using a secret."""

    key = "crypto.hash"
    title = "Hash"
    category = "data"
    config = [
        {"name": "text", "type": "string", "required": True},
        {
            "name": "algorithm",
            "type": "string",
            "enum": ["sha256", "sha512", "hmac-sha256"],
            "default": "sha256",
        },
        {"name": "secret", "type": "string", "description": "Secret name, for HMAC"},
    ]

    async def run(self, config, run):
        text = str(config.get("text") or "").encode()
        algorithm = config.get("algorithm") or "sha256"
        if algorithm == "hmac-sha256":
            secret = await run.runtime.secret(config.get("secret") or "")
            if not secret:
                raise FlowError("HMAC needs an existing secret", code="missing_secret")
            return BlockResult(output=hmac.new(secret.encode(), text, hashlib.sha256).hexdigest())
        return BlockResult(output=hashlib.new(algorithm, text).hexdigest())


class Now(Block):
    """The current UTC time, optionally shifted."""

    key = "time.now"
    title = "Current time"
    category = "data"
    config = [
        {"name": "offset_seconds", "type": "integer", "default": 0},
        {"name": "format", "type": "string", "enum": ["iso", "unix"], "default": "iso"},
    ]

    async def run(self, config, run):
        moment = datetime.now(UTC) + timedelta(seconds=int(config.get("offset_seconds") or 0))
        if config.get("format") == "unix":
            return BlockResult(output=int(moment.timestamp()))
        return BlockResult(output=moment.isoformat())


BLOCKS = [
    Transform,
    Template,
    Pick,
    Validate,
    JsonParse,
    JsonStringify,
    Calculate,
    GenerateId,
    Hash,
    Now,
]
