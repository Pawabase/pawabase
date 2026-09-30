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


async def test_time_now_formats():
    flow = {
        "nodes": [
            node("start", "trigger.manual"),
            node("iso", "time.now"),
            node("day", "time.now", format="date"),
            node("unix", "time.now", format="unix"),
        ],
        "edges": [edge("start", "iso"), edge("iso", "day"), edge("day", "unix")],
    }
    run = await run_flow(flow, runtime=FakeRuntime(), input={})
    steps = run.state["steps"]
    assert steps["iso"]["output"].startswith(steps["day"]["output"] + "T")
    assert len(steps["day"]["output"]) == 10
    assert isinstance(steps["unix"]["output"], int)


async def test_collection_blocks_filter_sort_group_and_batch():
    flow = {
        "nodes": [
            node("start", "trigger.manual"),
            node(
                "filter",
                "transform.filter",
                value="{{ input.items }}",
                condition={"gte": ["$item.score", 10]},
            ),
            node("sort", "transform.sort", value="{{ steps.filter.output }}", field="score"),
            node("group", "transform.group", value="{{ steps.sort.output }}", field="team"),
            node("batch", "transform.batch", value="{{ steps.sort.output }}", size=1),
        ],
        "edges": [
            edge("start", "filter"),
            edge("filter", "sort"),
            edge("sort", "group"),
            edge("group", "batch"),
        ],
    }
    run = await run_flow(
        flow,
        runtime=FakeRuntime(),
        input={
            "items": [
                {"name": "b", "score": 12, "team": "blue"},
                {"name": "a", "score": 4, "team": "blue"},
                {"name": "c", "score": 20, "team": "red"},
            ]
        },
    )
    assert [item["name"] for item in run.state["steps"]["sort"]["output"]] == ["b", "c"]
    assert set(run.state["steps"]["group"]["output"]) == {"blue", "red"}
    assert run.state["steps"]["batch"]["output"] == [
        [{"name": "b", "score": 12, "team": "blue"}],
        [{"name": "c", "score": 20, "team": "red"}],
    ]


async def test_timeout_block_uses_timeout_handle():
    flow = {
        "nodes": [
            node("start", "trigger.manual"),
            node("deadline", "control.timeout", seconds=0.01),
            node("work", "control.delay", seconds=0.1),
            node("timed_out", "control.set", values={"timed_out": True}),
        ],
        "edges": [
            edge("start", "deadline"),
            edge("deadline", "work", "attempt"),
            edge("deadline", "timed_out", "timeout"),
        ],
    }
    run = await run_flow(flow, runtime=FakeRuntime())
    assert run.state["vars"] == {"timed_out": True}
    assert run.state["steps"]["deadline"]["handle"] == "timeout"


async def test_while_block_repeats_body_and_has_iteration_guard():
    flow = {
        "nodes": [
            node("start", "trigger.manual"),
            node("init", "control.set", values={"index": 0}),
            node(
                "loop",
                "control.while",
                condition={"lt": ["$vars.index", 3]},
                max_iterations=10,
            ),
            node("increment", "math.calculate", operation="add", values=["{{ vars.index }}", 1]),
            node("save", "control.set", values={"index": "{{ steps.increment.output }}"}),
            node("done", "response.return", body="{{ vars.index }}"),
        ],
        "edges": [
            edge("start", "init"),
            edge("init", "loop"),
            edge("loop", "increment", "body"),
            edge("increment", "save"),
            edge("loop", "done", "done"),
        ],
    }
    run = await run_flow(flow, runtime=FakeRuntime())
    assert run.response.body == 3
    assert [item["index"] for item in run.state["steps"]["loop"]["output"]] == [1, 2, 3]

    flow["nodes"][2]["data"]["config"] = {
        "condition": True,
        "max_iterations": 2,
    }
    with pytest.raises(FlowError, match="exceeded 2 iterations"):
        await run_flow(flow, runtime=FakeRuntime())


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
