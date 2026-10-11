"""Presence replicated across instances."""

from app.realtime.presence import Member, PresenceRegistry


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def member(ref, instance="a", user="1", **meta):
    return Member(presence_ref=ref, user_id=user, meta=meta, instance=instance)


def test_join_is_idempotent_and_leave_returns_who_left():
    presence = PresenceRegistry("a", ttl=30)
    assert presence.join("dev/chat", member("p1", n=1))
    assert not presence.join("dev/chat", member("p1", n=1))  # same entry again changes nothing
    assert presence.join("dev/chat", member("p1", n=2))  # new meta is a change
    assert presence.members("dev/chat")[0]["meta"] == {"n": 2}
    assert presence.leave("dev/chat", "p1").presence_ref == "p1"
    assert presence.leave("dev/chat", "p1") is None
    assert presence.rooms() == []


def test_public_members_do_not_expose_the_owning_instance():
    presence = PresenceRegistry("a", ttl=30)
    presence.join("dev/chat", member("p1"))
    assert "instance" not in presence.members("dev/chat")[0]


def test_a_diff_from_another_instance_updates_the_replica():
    presence = PresenceRegistry("a", ttl=30)
    presence.apply(
        "dev/chat", [{"presence_ref": "r1", "user_id": "9", "meta": {"x": 1}}], [], origin="b"
    )
    assert presence.count("dev/chat") == 1 and not presence.owned("dev/chat", "r1")
    presence.apply("dev/chat", [], [{"presence_ref": "r1"}], origin="b")
    assert presence.count("dev/chat") == 0


def test_a_diff_with_a_leave_and_a_join_for_the_same_ref_ends_present():
    presence = PresenceRegistry("a", ttl=30)
    presence.apply("r", [{"presence_ref": "x", "meta": {"v": 1}}], [], "b")
    presence.apply("r", [{"presence_ref": "x", "meta": {"v": 2}}], [{"presence_ref": "x"}], "b")
    assert presence.members("r")[0]["meta"] == {"v": 2}


def test_malformed_entries_in_a_diff_are_skipped():
    presence = PresenceRegistry("a", ttl=30)
    presence.apply("r", [{"meta": {}}, {"presence_ref": ""}], [{"nope": 1}], "b")
    assert presence.count("r") == 0


def test_members_of_a_silent_instance_expire_but_a_live_one_keeps_its_own():
    clock = Clock()
    presence = PresenceRegistry("a", ttl=30, clock=clock)
    presence.join("r", member("mine", instance="a"))
    presence.apply("r", [{"presence_ref": "theirs", "meta": {}}], [], origin="b")
    presence.apply("r", [{"presence_ref": "other", "meta": {}}], [], origin="c")
    presence.heard_from("b")
    presence.heard_from("c")
    clock.now = 20
    presence.heard_from("c")  # c is still alive, b went quiet
    clock.now = 45
    removed = presence.expire()
    assert [m["presence_ref"] for m in removed["r"]] == ["theirs"]
    assert sorted(m["presence_ref"] for m in presence.members("r")) == ["mine", "other"]
    assert presence.instances() == ["a", "c"]


def test_own_heartbeats_are_not_tracked():
    presence = PresenceRegistry("a", ttl=1)
    presence.heard_from("a")
    assert presence.instances() == ["a"]
