"""Control blocks: conditions, branching, loops, variables, delays, retries.

Conditions use the policy condition language, with ``$path`` operands reading
the run's state::

    {"all": [{"eq": ["$input.status", "paid"]}, {"gt": ["$input.total", 100]}]}
"""

from __future__ import annotations

import asyncio
from typing import Any

from sillo.helpers.retry import async_retry

from ...policies.engine import evaluate, validate_condition
from ..engine import FlowError
from ..registry import Block, BlockResult

MAX_DELAY_SECONDS = 300
MAX_LOOP_ITEMS = 10_000


class If(Block):
    """Follows ``true`` or ``false`` depending on a condition."""

    key = "control.if"
    title = "If"
    category = "control"
    handles = ["true", "false"]
    raw_config = True
    config = [{"name": "condition", "type": "json", "required": True, "widget": "condition"}]

    async def run(self, config, run):
        condition = config.get("condition", False)
        validate_condition(condition)
        outcome = evaluate(condition, run.state)
        return BlockResult(output=outcome, handle="true" if outcome else "false")


class Switch(Block):
    """Follows the handle named by a value, or ``default``."""

    key = "control.switch"
    title = "Switch"
    category = "control"
    handles = ["*"]
    config = [
        {
            "name": "value",
            "type": "string",
            "required": True,
            "description": "Template whose value picks the branch",
        },
        {"name": "cases", "type": "array", "items": {"type": "string"}},
    ]

    async def run(self, config, run):
        value = config.get("value")
        cases = [str(case) for case in config.get("cases") or []]
        handle = str(value) if (not cases or str(value) in cases) else "default"
        return BlockResult(output=value, handle=handle)


class ForEach(Block):
    """Runs the ``each`` branch once per item, then follows ``done``.

    Inside the body, ``item`` and ``index`` hold the current element. The block's
    output is the list of the body's last outputs.
    """

    key = "control.foreach"
    title = "For each"
    category = "control"
    handles = ["each", "done"]
    config = [
        {
            "name": "items",
            "type": "json",
            "required": True,
            "description": "Template resolving to a list",
        },
        {"name": "concurrency", "type": "integer", "default": 1, "minimum": 1, "maximum": 1},
    ]

    async def run(self, config, run):
        items = config.get("items") or []
        if isinstance(items, dict):
            items = list(items.items())
        if not isinstance(items, list):
            raise FlowError("for each needs a list", status=500, code="not_a_list")
        if len(items) > MAX_LOOP_ITEMS:
            raise FlowError(f"for each is limited to {MAX_LOOP_ITEMS} items", code="too_many_items")
        node_id = run.current_node
        results = []
        for index, item in enumerate(items):
            results.append(await run.run_branch(node_id, "each", {"item": item, "index": index}))
            if run.stopped:
                break
        return BlockResult(output=results, handle="done")


class While(Block):
    """Runs the ``body`` branch while a condition is true, then follows ``done``."""

    key = "control.while"
    title = "While"
    category = "control"
    handles = ["body", "done"]
    raw_config = True
    config = [
        {
            "name": "condition",
            "type": "json",
            "required": True,
            "widget": "condition",
            "description": "Condition evaluated before every iteration",
        },
        {
            "name": "max_iterations",
            "type": "integer",
            "default": 1000,
            "minimum": 1,
            "maximum": 10000,
            "description": "Safety limit for this loop",
        },
    ]

    async def run(self, config, run):
        condition = config.get("condition", False)
        validate_condition(condition)
        max_iterations = int(config.get("max_iterations") or 1000)
        if max_iterations < 1 or max_iterations > 10_000:
            raise FlowError("max_iterations must be between 1 and 10000", code="bad_config")

        node_id = run.current_node
        outputs = []
        iterations = 0
        while evaluate(condition, run.state):
            if iterations >= max_iterations:
                raise FlowError(
                    f"while loop exceeded {max_iterations} iterations",
                    code="too_many_iterations",
                )
            outputs.append(await run.run_branch(node_id, "body"))
            iterations += 1
            if run.stopped:
                break
        return BlockResult(output=outputs, handle="done")


class SetVariables(Block):
    """Sets ``vars.<name>`` for later blocks."""

    key = "control.set"
    title = "Set variables"
    category = "control"
    config = [
        {
            "name": "values",
            "type": "json",
            "required": True,
            "description": "Object of name → value (templates allowed)",
        }
    ]

    async def run(self, config, run):
        values = config.get("values") or {}
        if not isinstance(values, dict):
            raise FlowError("values must be an object", code="bad_config")
        run.state["vars"].update(values)
        return BlockResult(output=values)


class Merge(Block):
    """Merges several objects into one (later keys win)."""

    key = "control.merge"
    title = "Merge objects"
    category = "control"
    config = [
        {"name": "objects", "type": "json", "required": True, "description": "List of objects"}
    ]

    async def run(self, config, run):
        merged: dict[str, Any] = {}
        for item in config.get("objects") or []:
            if isinstance(item, dict):
                merged.update(item)
        return BlockResult(output=merged)


class NoOp(Block):
    """Continues the flow without changing state or doing work."""

    key = "control.noop"
    title = "Do nothing"
    category = "control"

    async def run(self, config, run):
        return BlockResult(output=None)


class Delay(Block):
    """Waits before continuing. Long waits belong in a delayed job instead."""

    key = "control.delay"
    title = "Delay"
    category = "control"
    config = [
        {
            "name": "seconds",
            "type": "number",
            "required": True,
            "minimum": 0,
            "maximum": MAX_DELAY_SECONDS,
        }
    ]

    async def run(self, config, run):
        seconds = float(config.get("seconds") or 0)
        if seconds > MAX_DELAY_SECONDS:
            raise FlowError(
                f"delays are limited to {MAX_DELAY_SECONDS}s; dispatch a delayed job instead",
                code="delay_too_long",
            )
        await asyncio.sleep(seconds)
        return BlockResult(output=seconds)


class Stop(Block):
    """Ends the run without a response."""

    key = "control.stop"
    title = "Stop"
    category = "control"
    handles = []

    async def run(self, config, run):
        return BlockResult(output=None, stop=True)


class Retry(Block):
    """Runs the ``attempt`` branch until it succeeds, with backoff.

    Uses Sillo's retry helper, so the backoff and jitter are Sillo's.
    """

    key = "control.retry"
    title = "Retry"
    category = "control"
    handles = ["attempt", "next"]
    config = [
        {"name": "attempts", "type": "integer", "default": 3, "minimum": 1, "maximum": 10},
        {"name": "base_delay", "type": "number", "default": 0.5, "minimum": 0, "maximum": 30},
    ]

    async def run(self, config, run):
        node_id = run.current_node
        attempts = int(config.get("attempts") or 3)

        async def attempt():
            return await run.run_branch(node_id, "attempt")

        output = await async_retry(
            attempt,
            max_attempts=attempts,
            base_delay=float(config.get("base_delay", 0.5)),
            max_delay=30.0,
        )
        return BlockResult(output=output)


class Timeout(Block):
    """Runs an ``attempt`` branch with a deadline, then follows ``next`` or ``timeout``."""

    key = "control.timeout"
    title = "Timeout"
    category = "control"
    handles = ["attempt", "next", "timeout"]
    config = [
        {
            "name": "seconds",
            "type": "number",
            "required": True,
            "minimum": 0.001,
            "maximum": 300,
            "description": "Maximum time allowed for the attempt branch",
        }
    ]

    async def run(self, config, run):
        seconds = float(config.get("seconds") or 0)
        if seconds <= 0 or seconds > 300:
            raise FlowError("timeout must be between 0.001 and 300 seconds", code="bad_config")
        try:
            output = await asyncio.wait_for(
                run.run_branch(run.current_node, "attempt"), timeout=seconds
            )
        except TimeoutError:
            return BlockResult(output={"timeout_seconds": seconds}, handle="timeout")
        return BlockResult(output=output)


BLOCKS = [If, Switch, ForEach, While, SetVariables, Merge, NoOp, Delay, Stop, Retry, Timeout]
