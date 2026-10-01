"""Data blocks: transformation, validation, JSON, text, maths, ids, hashing, time."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from ...policies.engine import evaluate, lookup, validate_condition
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


class RenameFields(Block):
    """Renames top-level fields on an object or every object in a list."""

    key = "transform.rename"
    title = "Rename fields"
    category = "data"
    config = [
        {"name": "value", "type": "json", "required": True},
        {"name": "fields", "type": "json", "required": True, "description": "Map old field names to new field names"},
    ]

    async def run(self, config, run):
        value = config.get("value")
        fields = config.get("fields")
        if not isinstance(fields, dict):
            raise FlowError("rename fields needs a field map", code="bad_config")

        def rename(item):
            if not isinstance(item, dict):
                raise FlowError("rename fields needs objects", code="not_an_object")
            output = dict(item)
            for old, new in fields.items():
                if old in output:
                    output[str(new)] = output.pop(old)
            return output

        return BlockResult(output=[rename(item) for item in value] if isinstance(value, list) else rename(value))


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
        {
            "name": "format",
            "type": "string",
            "enum": ["iso", "date", "unix"],
            "default": "iso",
            "description": "iso: 2026-09-28T08:00:00+00:00 · date: 2026-09-28 · unix: seconds",
        },
    ]

    async def run(self, config, run):
        moment = datetime.now(UTC) + timedelta(seconds=int(config.get("offset_seconds") or 0))
        if config.get("format") == "unix":
            return BlockResult(output=int(moment.timestamp()))
        if config.get("format") == "date":
            return BlockResult(output=moment.date().isoformat())
        return BlockResult(output=moment.isoformat())


class Filter(Block):
    """Keeps list items matching a policy-style condition."""

    key = "transform.filter"
    title = "Filter list"
    category = "data"
    raw_config = True
    config = [
        {"name": "value", "type": "json", "required": True},
        {
            "name": "condition",
            "type": "json",
            "required": True,
            "widget": "condition",
            "description": "Use $item and $index to inspect each list item",
        },
    ]

    async def run(self, config, run):
        value = run.render(config.get("value"))
        if not isinstance(value, list):
            raise FlowError("filter needs a list", code="not_a_list")
        condition = config.get("condition", False)
        validate_condition(condition)
        matches = []
        for index, item in enumerate(value):
            context = {**run.state, "item": item, "index": index}
            if evaluate(condition, context):
                matches.append(item)
        return BlockResult(output=matches)


class Sort(Block):
    """Sorts a list by a field or dotted path."""

    key = "transform.sort"
    title = "Sort list"
    category = "data"
    config = [
        {"name": "value", "type": "json", "required": True},
        {"name": "field", "type": "string", "required": True},
        {"name": "descending", "type": "boolean", "default": False},
    ]

    async def run(self, config, run):
        value = config.get("value")
        if not isinstance(value, list):
            raise FlowError("sort needs a list", code="not_a_list")
        field = str(config.get("field") or "")
        if not field:
            raise FlowError("sort needs a field", code="bad_config")
        try:
            output = sorted(value, key=lambda item: (lookup(item, field) is None, lookup(item, field)))
        except TypeError:
            output = sorted(value, key=lambda item: str(lookup(item, field) or ""))
        if config.get("descending"):
            output.reverse()
        return BlockResult(output=output)


class Group(Block):
    """Groups a list into an object keyed by a field or dotted path."""

    key = "transform.group"
    title = "Group list"
    category = "data"
    config = [
        {"name": "value", "type": "json", "required": True},
        {"name": "field", "type": "string", "required": True},
    ]

    async def run(self, config, run):
        value = config.get("value")
        if not isinstance(value, list):
            raise FlowError("group needs a list", code="not_a_list")
        field = str(config.get("field") or "")
        if not field:
            raise FlowError("group needs a field", code="bad_config")
        groups: dict[str, list[Any]] = {}
        for item in value:
            key = str(lookup(item, field))
            groups.setdefault(key, []).append(item)
        return BlockResult(output=groups)


class Unique(Block):
    """Removes duplicate list items, optionally using a field or dotted path."""

    key = "transform.unique"
    title = "Remove duplicates"
    category = "data"
    config = [
        {"name": "value", "type": "json", "required": True},
        {"name": "field", "type": "string", "description": "Field or dotted path used to identify duplicates"},
    ]

    async def run(self, config, run):
        value = config.get("value")
        if not isinstance(value, list):
            raise FlowError("remove duplicates needs a list", code="not_a_list")
        field = config.get("field")
        seen = set()
        output = []
        for item in value:
            candidate = lookup(item, field) if field else item
            try:
                key = json.dumps(candidate, sort_keys=True, default=str)
            except (TypeError, ValueError):
                key = repr(candidate)
            if key not in seen:
                seen.add(key)
                output.append(item)
        return BlockResult(output=output)


class Summarize(Block):
    """Summarizes a list, optionally once per group."""

    key = "transform.summarize"
    title = "Summarize list"
    category = "data"
    config = [
        {"name": "value", "type": "json", "required": True},
        {
            "name": "operation",
            "type": "string",
            "enum": [
                "count", "count_distinct", "sum", "average", "min", "max", "median",
                "mode", "first", "last", "collect", "distinct",
            ],
            "required": True,
            "description": "The aggregation to calculate",
        },
        {"name": "field", "type": "string", "description": "Field or dotted path to summarize"},
        {"name": "group_by", "type": "string", "description": "Field or dotted path to group by"},
        {"name": "ignore_null", "type": "boolean", "default": True},
    ]

    async def run(self, config, run):
        value = config.get("value")
        if not isinstance(value, list):
            raise FlowError("summarize needs a list", code="not_a_list")
        field = config.get("field")
        operation = config.get("operation")
        ignore_null = config.get("ignore_null", True)

        def values(items):
            result = [lookup(item, field) if field else item for item in items]
            return [item for item in result if item is not None] if ignore_null else result

        def identity(item):
            try:
                return json.dumps(item, sort_keys=True, default=str)
            except (TypeError, ValueError):
                return repr(item)

        def summarize(items):
            extracted = values(items)
            if operation == "count":
                return len(extracted) if field and ignore_null else len(items)
            if operation in {"count_distinct", "distinct"}:
                unique = list(dict.fromkeys(identity(item) for item in extracted))
                if operation == "count_distinct":
                    return len(unique)
                seen = set()
                return [item for item in extracted if not (identity(item) in seen or seen.add(identity(item)))]
            if operation == "collect":
                return extracted
            if operation in {"first", "last"}:
                if not extracted:
                    return None
                return extracted[0] if operation == "first" else extracted[-1]
            try:
                numbers = [float(item) for item in extracted]
            except (TypeError, ValueError) as exc:
                raise FlowError(f"{operation} needs numeric values", code="not_a_number") from exc
            if not numbers:
                return None
            if operation == "sum":
                result = sum(numbers)
            elif operation == "average":
                result = sum(numbers) / len(numbers)
            elif operation == "min":
                result = min(numbers)
            elif operation == "max":
                result = max(numbers)
            elif operation == "median":
                ordered = sorted(numbers)
                middle = len(ordered) // 2
                result = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
            elif operation == "mode":
                counts: dict[float, int] = {}
                for number in numbers:
                    counts[number] = counts.get(number, 0) + 1
                result = max(numbers, key=lambda number: counts[number])
            else:
                raise FlowError(f"unknown summary operation {operation!r}", code="bad_config")
            return int(result) if isinstance(result, float) and result.is_integer() else result

        group_by = config.get("group_by")
        if not group_by:
            return BlockResult(output=summarize(value))
        groups: dict[str, list[Any]] = {}
        for item in value:
            groups.setdefault(str(lookup(item, group_by)), []).append(item)
        return BlockResult(output={key: summarize(items) for key, items in groups.items()})


class Batch(Block):
    """Splits a list into bounded batches for downstream processing."""

    key = "transform.batch"
    title = "Batch list"
    category = "data"
    config = [
        {"name": "value", "type": "json", "required": True},
        {"name": "size", "type": "integer", "required": True, "minimum": 1, "maximum": 1000},
    ]

    async def run(self, config, run):
        value = config.get("value")
        if not isinstance(value, list):
            raise FlowError("batch needs a list", code="not_a_list")
        size = int(config.get("size") or 0)
        if size < 1 or size > 1000:
            raise FlowError("batch size must be between 1 and 1000", code="bad_config")
        return BlockResult(output=[value[index : index + size] for index in range(0, len(value), size)])


class Flatten(Block):
    """Flattens nested lists by a selected number of levels."""

    key = "transform.flatten"
    title = "Flatten list"
    category = "data"
    config = [
        {"name": "value", "type": "json", "required": True},
        {"name": "depth", "type": "integer", "default": 1, "minimum": 1, "maximum": 20},
    ]

    async def run(self, config, run):
        value = config.get("value")
        if not isinstance(value, list):
            raise FlowError("flatten needs a list", code="not_a_list")
        depth = int(config.get("depth") or 1)
        if not 1 <= depth <= 20:
            raise FlowError("flatten depth must be between 1 and 20", code="bad_config")
        output = value
        for _ in range(depth):
            output = [child for item in output for child in (item if isinstance(item, list) else [item])]
        return BlockResult(output=output)


class Slice(Block):
    """Selects a window of a list for paging or limiting results."""

    key = "transform.slice"
    title = "Slice list"
    category = "data"
    config = [
        {"name": "value", "type": "json", "required": True},
        {"name": "offset", "type": "integer", "default": 0},
        {"name": "limit", "type": "integer", "description": "Maximum number of items"},
    ]

    async def run(self, config, run):
        value = config.get("value")
        if not isinstance(value, list):
            raise FlowError("slice needs a list", code="not_a_list")
        offset = int(config.get("offset") or 0)
        limit = config.get("limit")
        if offset < 0 or (limit is not None and int(limit) < 0):
            raise FlowError("slice offset and limit cannot be negative", code="bad_config")
        return BlockResult(output=value[offset:] if limit is None else value[offset : offset + int(limit)])


BLOCKS = [
    Transform,
    Template,
    Pick,
    RenameFields,
    Validate,
    JsonParse,
    JsonStringify,
    Calculate,
    GenerateId,
    Hash,
    Now,
    Filter,
    Sort,
    Group,
    Unique,
    Summarize,
    Batch,
    Flatten,
    Slice,
]
