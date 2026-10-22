"""The rules tester explains a decision with the same evaluation the data API runs."""

from pawabase_server.policies.engine import evaluate, mentions, trace

CONTEXT = {
    "auth": {"authenticated": True, "user_id": "u1", "roles": ["editor"], "permissions": []},
    "record": {"owner_id": "u2", "status": "draft"},
}


def test_trace_agrees_with_evaluate():
    for condition in (
        {"role": "editor"},
        {"owner": "owner_id"},
        {"all": [{"authenticated": True}, {"eq": ["$record.status", "draft"]}]},
        {"any": [{"role": "admin"}, {"eq": ["$record.owner_id", "$auth.user_id"]}]},
        {"not": {"role": "editor"}},
        True,
        False,
    ):
        assert trace(condition, CONTEXT)["result"] == evaluate(condition, CONTEXT)


def test_a_comparison_shows_both_sides():
    node = trace({"eq": ["$record.owner_id", "$auth.user_id"]}, CONTEXT)
    assert node["result"] is False
    assert node["left"] == {"expr": "$record.owner_id", "value": "u2"}
    assert node["right"] == {"expr": "$auth.user_id", "value": "u1"}


def test_a_literal_has_no_expression():
    node = trace({"eq": ["$record.status", "draft"]}, CONTEXT)
    assert node["right"] == {"expr": None, "value": "draft"}


def test_a_shorthand_carries_what_it_stands_for():
    node = trace({"owner": "owner_id"}, CONTEXT)
    assert node["op"] == "owner" and node["result"] is False
    (child,) = node["children"]
    assert child["op"] == "all"
    assert [c["result"] for c in child["children"]] == [True, False]


def test_a_missing_record_reads_as_empty():
    node = trace({"exists": "$record.owner_id"}, {"auth": {}})
    assert node["result"] is False and node["operand"]["value"] is None


def test_mentions_finds_what_a_rule_reads():
    assert mentions({"owner": "owner_id"}, "record")
    assert mentions({"all": [{"role": "a"}, {"eq": ["$input.x", 1]}]}, "input")
    assert not mentions({"role": "admin"}, "record")
    assert not mentions({"eq": ["$auth.user_id", "x"]}, "record")
