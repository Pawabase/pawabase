"""Running a Flow.

A Flow is stored in the node/edge shape of ``@xyflow/react``, so Studio's editor
reads and writes it directly::

    {"nodes": [{"id": "start", "data": {"block": "trigger.http", "config": {}}},
               {"id": "load",  "data": {"block": "resource.get",
                                        "config": {"resource": "posts", "id": "{{ input.id }}"}}},
               {"id": "reply", "data": {"block": "response.return",
                                        "config": {"body": "{{ steps.load.output }}"}}}],
     "edges": [{"source": "start", "target": "load"},
               {"source": "load",  "target": "reply"}]}

A run starts at a trigger node and follows edges by *handle*: a block returns
``next``, ``true``/``false``, a switch case, ``each``/``done`` or ``error``, and the
edges whose ``sourceHandle`` matches are followed in order. A block that
raises follows its ``error`` edge if it has one, and otherwise fails the run.

Blocks share state:

* ``input``: what triggered the run;
* ``auth``, ``env``: who and where;
* ``vars``: values set with ``control.set``;
* ``steps.<node id>.output``: each finished block's output;
* ``item`` and ``index``: inside a loop body;
* ``error``: inside an error branch.
"""

from __future__ import annotations

import asyncio
import copy
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..ids import new_ulid
from ..templating import render
from .registry import BlockRegistry, BlockResult, default_registry
from .runtime import BaseRuntime, Runtime

MAX_STEPS = 10_000
TRACE_OUTPUT_LIMIT = 2_000
TRACE_COLLECTION_LIMIT = 100
SENSITIVE_KEY = re.compile(
    r"(^|[_-])(authorization|cookie|password|passwd|secret|token|api[_-]?key|private[_-]?key)($|[_-])",
    re.IGNORECASE,
)


class FlowError(Exception):
    """A run failed, or a flow chose to fail (``error.raise``).

    Attributes:
        status: HTTP status to answer with when the flow serves a request.
        code: A stable machine-readable code.
        node: Where it happened.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int = 500,
        code: str = "flow_error",
        node: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.node = node
        self.details = details


class FlowDefinitionError(ValueError):
    """A flow graph that cannot run: unknown blocks, dangling edges, no trigger."""


@dataclass
class Step:
    """One executed block, recorded for inspection."""

    node: str
    block: str
    started_at: float
    sequence: int = 0
    input: Any = None
    duration_ms: float = 0.0
    handle: str | None = None
    output: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "block": self.block,
            "sequence": self.sequence,
            "started_at": self.started_at,
            "duration_ms": round(self.duration_ms, 3),
            "status": "failed" if self.error else "succeeded",
            "handle": self.handle,
            "input": observable(self.input),
            "output": observable(self.output),
            "error": self.error,
        }


def observable(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded, JSON-safe value with common credential fields redacted."""
    if depth >= 8:
        return "[depth limit]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        items = list(value.items())
        for key, item in items[:TRACE_COLLECTION_LIMIT]:
            name = str(key)
            result[name] = (
                "[redacted]" if SENSITIVE_KEY.search(name) else observable(item, depth=depth + 1)
            )
        if len(items) > TRACE_COLLECTION_LIMIT:
            result["…"] = f"{len(items) - TRACE_COLLECTION_LIMIT} more fields"
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [observable(item, depth=depth + 1) for item in items[:TRACE_COLLECTION_LIMIT]]
        if len(items) > TRACE_COLLECTION_LIMIT:
            result.append(f"[{len(items) - TRACE_COLLECTION_LIMIT} more items]")
        return result
    if isinstance(value, str):
        return value if len(value) <= TRACE_OUTPUT_LIMIT else value[:TRACE_OUTPUT_LIMIT] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _truncate(value)


def _truncate(value: Any) -> Any:
    text = repr(value)
    if len(text) <= TRACE_OUTPUT_LIMIT:
        try:
            import json

            json.dumps(value, default=str)
            return value
        except (TypeError, ValueError):
            return text
    return text[:TRACE_OUTPUT_LIMIT] + "…"


@dataclass
class FlowResponse:
    """What ``response.return`` produced, for an HTTP-triggered flow."""

    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)


class FlowRun:
    """One execution of a flow.

    Args:
        flow: The flow definition (``nodes``/``edges``).
        runtime: Platform capabilities for blocks.
        input: The trigger payload.
        auth: The caller's policy context.
        env: The environment the run belongs to.
        secrets: Resolved lazily through the runtime, never stored in traces.
        registry: Blocks; the process default when omitted.
        trigger: Which trigger node to start from, when a flow has several.
        name: For traces.
    """

    def __init__(
        self,
        flow: Mapping[str, Any],
        *,
        runtime: Runtime | None = None,
        input: Any = None,
        auth: Mapping[str, Any] | None = None,
        env: str | None = None,
        registry: BlockRegistry | None = None,
        trigger: str | None = None,
        name: str = "flow",
        timeout: float = 60.0,
    ) -> None:
        self.id = new_ulid()
        self.name = name
        self.flow = flow
        self.runtime: Runtime = runtime or BaseRuntime()  # type: ignore[assignment]
        self.registry = registry or default_registry()
        self.timeout = timeout
        self.state: dict[str, Any] = {
            "input": input,
            "auth": dict(auth or {}),
            "env": env,
            "vars": {},
            "steps": {},
            "run": {"id": self.id, "flow": name},
        }
        self.steps: list[Step] = []
        self.response: FlowResponse | None = None
        self.stopped = False
        self.logs: list[dict[str, Any]] = []
        self._nodes = {node["id"]: node for node in flow.get("nodes", [])}
        self._edges: dict[str, list[Mapping[str, Any]]] = {}
        for edge in flow.get("edges", []):
            self._edges.setdefault(edge["source"], []).append(edge)
        self._trigger = trigger
        self._count = 0
        #: The node being executed; loop and retry blocks read it to find their body.
        self.current_node: str | None = None

    # ── public ─────────────────────────────────────────────────────────

    async def execute(self) -> FlowRun:
        """Run to completion. Raises :class:`FlowError` on failure."""
        start = self.entry_node()
        try:
            await asyncio.wait_for(self._walk(start), timeout=self.timeout)
        except TimeoutError as exc:
            raise FlowError(f"flow exceeded {self.timeout}s", status=504, code="timeout") from exc
        return self

    def entry_node(self) -> str:
        if self._trigger:
            if self._trigger not in self._nodes:
                raise FlowDefinitionError(f"no node {self._trigger!r}")
            return self._trigger
        for node_id, node in self._nodes.items():
            block = self.registry.get(block_key(node))
            if block.trigger:
                return node_id
        raise FlowDefinitionError("the flow has no trigger block")

    def render(self, value: Any, extra: Mapping[str, Any] | None = None) -> Any:
        """Render templates in *value* against the run's state."""
        state = self.state if not extra else {**self.state, **extra}
        return render(value, state)

    def result(self) -> Any:
        """The run's value: the response body, or the last step's output."""
        if self.response is not None:
            return self.response.body
        return self.steps[-1].output if self.steps else None

    def trace(self) -> list[dict[str, Any]]:
        return [step.to_dict() for step in self.steps]

    # ── walking ────────────────────────────────────────────────────────

    async def _walk(self, node_id: str) -> None:
        # Depth-first with an explicit stack: a long chain of blocks must not
        # be limited by Python's recursion depth.
        stack = [node_id]
        while stack and not self.stopped:
            current = stack.pop()
            handle = await self._execute_node(current)
            if handle is None:
                continue
            stack.extend(reversed(self.targets(current, handle)))

    def targets(self, node_id: str, handle: str) -> list[str]:
        """Nodes reached from *node_id* through *handle*."""
        found = []
        for edge in self._edges.get(node_id, []):
            edge_handle = edge.get("sourceHandle") or "next"
            if edge_handle == handle:
                found.append(edge["target"])
        return found

    async def run_branch(
        self, node_id: str, handle: str, extra: Mapping[str, Any] | None = None
    ) -> Any:
        """Run everything reachable through *handle* (a loop body, say).

        Returns:
            The output of the last step the branch ran.
        """
        saved = {key: self.state.get(key) for key in (extra or {})}
        if extra:
            self.state.update(extra)
        before = len(self.steps)
        try:
            for target in self.targets(node_id, handle):
                await self._walk(target)
                if self.stopped:
                    break
        finally:
            for key, value in saved.items():
                if value is None:
                    self.state.pop(key, None)
                else:
                    self.state[key] = value
        return self.steps[-1].output if len(self.steps) > before else None

    async def _execute_node(self, node_id: str) -> str | None:
        self._count += 1
        if self._count > MAX_STEPS:
            raise FlowError(f"flow exceeded {MAX_STEPS} steps", code="too_many_steps", node=node_id)
        node = self._nodes.get(node_id)
        if node is None:
            raise FlowDefinitionError(f"edge points at missing node {node_id!r}")
        key = block_key(node)
        block = self.registry.get(key)()
        raw = copy.deepcopy(node.get("data", {}).get("config", {}) or {})
        step = Step(node=node_id, block=key, started_at=time.time(), sequence=self._count)
        self.current_node = node_id
        started = time.perf_counter()
        try:
            config = raw if block.raw_config else self.render(raw)
            step.input = config
            result = await block.run(config, self)
            if not isinstance(result, BlockResult):
                result = BlockResult(output=result)
        except FlowError as exc:
            exc.node = exc.node or node_id
            step.error = exc.message
            step.duration_ms = (time.perf_counter() - started) * 1000
            self.steps.append(step)
            if self.targets(node_id, "error") and exc.code != "raised":
                return await self._handle_error(node_id, exc)
            raise
        except Exception as exc:  # a block failing is data for the error branch
            step.error = f"{type(exc).__name__}: {exc}"
            step.duration_ms = (time.perf_counter() - started) * 1000
            self.steps.append(step)
            if self.targets(node_id, "error"):
                return await self._handle_error(node_id, exc)
            raise FlowError(
                str(exc) or type(exc).__name__, node=node_id, code="block_failed"
            ) from exc
        step.duration_ms = (time.perf_counter() - started) * 1000
        step.handle = result.handle
        step.output = result.output
        self.steps.append(step)
        self.state["steps"][node_id] = {"output": result.output, "handle": result.handle}
        if result.stop:
            self.stopped = True
            return None
        return result.handle

    async def _handle_error(self, node_id: str, exc: Exception) -> str:
        self.state["error"] = {
            "message": getattr(exc, "message", str(exc)),
            "type": type(exc).__name__,
            "node": node_id,
        }
        return "error"


def block_key(node: Mapping[str, Any]) -> str:
    """The block a node runs: ``data.block``, falling back to the node type."""
    return str((node.get("data") or {}).get("block") or node.get("type"))


def validate_flow(flow: Mapping[str, Any], registry: BlockRegistry | None = None) -> list[str]:
    """Problems that would stop *flow* from running. Empty when it can run."""
    registry = registry or default_registry()
    problems: list[str] = []
    nodes = flow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return ["a flow needs at least one node"]
    ids = set()
    triggers = 0
    for node in nodes:
        node_id = node.get("id")
        if not node_id:
            problems.append("every node needs an id")
            continue
        if node_id in ids:
            problems.append(f"duplicate node id {node_id!r}")
        ids.add(node_id)
        key = block_key(node)
        if key not in registry:
            problems.append(f"node {node_id!r} uses unknown block {key!r}")
            continue
        block = registry.get(key)
        triggers += block.trigger
    for edge in flow.get("edges", []) or []:
        if edge.get("source") not in ids or edge.get("target") not in ids:
            problems.append(
                f"edge {edge.get('source')!r} → {edge.get('target')!r} references a missing node"
            )
            continue
        source = next(n for n in nodes if n.get("id") == edge["source"])
        key = block_key(source)
        if key in registry:
            handles = registry.get(key).handles
            handle = edge.get("sourceHandle") or "next"
            if handles != ["*"] and handle not in handles and handle != "error":
                problems.append(f"block {key!r} has no {handle!r} output")
    if triggers == 0:
        problems.append("a flow needs a trigger block")
    return problems


async def run_flow(flow: Mapping[str, Any], **kwargs: Any) -> FlowRun:
    """Build and execute a :class:`FlowRun`."""
    return await FlowRun(flow, **kwargs).execute()
