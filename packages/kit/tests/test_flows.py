import pytest

from pawabase_kit.flows import (
    BaseRuntime,
    FlowError,
    FlowRun,
    default_registry,
    run_flow,
    validate_flow,
)


def node(node_id, block, **config):
    return {"id": node_id, "data": {"block": block, "config": config}}


def edge(source, target, handle=None):
    item = {"source": source, "target": target}
    if handle:
        item["sourceHandle"] = handle
    return item


class FakeRuntime(BaseRuntime):
    def __init__(self):
        self.records = {"1": {"id": "1", "title": "hello", "total": 150}}
        self.emitted = []
        self.cache = {}

    async def resource_get(self, resource, record_id):
        return self.records.get(str(record_id))

    async def emit(self, name, payload):
        self.emitted.append((name, payload))
        return "evt"

    async def cache_get(self, key):
        return self.cache.get(key)

    async def cache_set(self, key, value, ttl=None, tags=None):
        self.cache[key] = value

    async def log(self, level, message, data=None):
        pass


def test_builtin_catalogue_is_substantial():
    registry = default_registry()
    assert len(registry) >= 50
    categories = {entry["category"] for entry in registry.catalogue()}
    assert {
        "triggers",
        "control",
        "data",
        "resources",
        "cache",
        "events",
        "jobs",
        "realtime",
        "storage",
        "mail",
        "integrations",
        "auth",
        "responses",
        "observability",
        "code",
    } <= categories


async def test_branching_flow_with_response():
    flow = {
        "nodes": [
            node("start", "trigger.http"),
            node("load", "resource.get", resource="orders", id="{{ input.id }}"),
            node("big", "control.if", condition={"gt": ["$steps.load.output.total", 100]}),
            node("emit", "event.emit", event="order.big", payload="{{ steps.load.output }}"),
            node(
                "yes",
                "response.return",
                status=200,
                body={"big": True, "title": "{{ steps.load.output.title }}"},
            ),
            node("no", "response.return", status=200, body={"big": False}),
            node("missing", "error.raise", status=404, message="no such order"),
        ],
        "edges": [
            edge("start", "load"),
            edge("load", "big"),
            edge("load", "missing", "missing"),
            edge("big", "emit", "true"),
            edge("emit", "yes"),
            edge("big", "no", "false"),
        ],
    }
    assert validate_flow(flow) == []
    runtime = FakeRuntime()
    run = await run_flow(flow, runtime=runtime, input={"id": "1"})
    assert run.response.status == 200 and run.response.body == {"big": True, "title": "hello"}
    assert runtime.emitted == [("order.big", {"id": "1", "title": "hello", "total": 150})]
    assert [step["node"] for step in run.trace()] == ["start", "load", "big", "emit", "yes"]

    with pytest.raises(FlowError) as raised:
        await run_flow(flow, runtime=runtime, input={"id": "9"})
    assert raised.value.status == 404


async def test_loop_variables_and_cache():
    flow = {
        "nodes": [
            node("start", "trigger.manual"),
            node("each", "control.foreach", items="{{ input.numbers }}"),
            node("double", "math.calculate", operation="multiply", values=["{{ item }}", 2]),
            node("sum", "math.calculate", operation="sum", values="{{ steps.each.output }}"),
            node("miss", "cache.get", key="total"),
            node("store", "cache.set", key="total", value="{{ steps.sum.output }}"),
        ],
        "edges": [
            edge("start", "each"),
            edge("each", "double", "each"),
            edge("each", "sum", "done"),
            edge("sum", "miss"),
            edge("miss", "store", "miss"),
        ],
    }
    runtime = FakeRuntime()
    run = await run_flow(flow, runtime=runtime, input={"numbers": [1, 2, 3]})
    assert run.state["steps"]["each"]["output"] == [2, 4, 6]
    assert runtime.cache["total"] == 12


async def test_error_branch_and_unavailable_capability():
    flow = {
        "nodes": [
            node("start", "trigger.manual"),
            node("mail", "mail.send", to="a@b.c", subject="hi", text="x"),
            node("fallback", "control.set", values={"failed": "{{ error.message }}"}),
        ],
        "edges": [edge("start", "mail"), edge("mail", "fallback", "error")],
    }
    run = await run_flow(flow, runtime=FakeRuntime())
    assert "not available" in run.state["vars"]["failed"]

    del flow["edges"][1]
    with pytest.raises(FlowError) as raised:
        await run_flow(flow, runtime=FakeRuntime())
    assert raised.value.node == "mail"


def test_validation_reports_problems():
    problems = validate_flow({"nodes": [node("a", "nope")], "edges": [edge("a", "b")]})
    assert any("unknown block" in p for p in problems)
    assert any("missing node" in p for p in problems)
    assert validate_flow({"nodes": [node("a", "control.set", values={})]}) == [
        "a flow needs a trigger block"
    ]
    bad_handle = {
        "nodes": [node("t", "trigger.manual"), node("x", "control.set", values={})],
        "edges": [edge("t", "x", "sideways")],
    }
    assert any("no 'sideways' output" in p for p in validate_flow(bad_handle))


async def test_step_limit_guards_against_cycles():
    flow = {
        "nodes": [
            node("start", "trigger.manual"),
            node("a", "control.set", values={}),
            node("b", "control.set", values={}),
        ],
        "edges": [edge("start", "a"), edge("a", "b"), edge("b", "a")],
    }
    with pytest.raises((FlowError, RecursionError)):
        await FlowRun(flow, runtime=FakeRuntime(), timeout=5).execute()
