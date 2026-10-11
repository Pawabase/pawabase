"""Channel and event names, and parsing client messages."""

import json

import pytest

from pawabase_server.realtime.names import (
    ChannelNameError,
    check_channel,
    check_event,
    room_name,
    split_room,
)
from pawabase_server.realtime.protocol import codes
from pawabase_server.realtime.protocol.messages import ProtocolError, parse


@pytest.mark.parametrize(
    "name", ["chat", "chat:lobby", "user:42", "a.b-c_d", "resource:orders", "x" * 200]
)
def test_good_channel_names_pass(name):
    assert check_channel(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "a/b",
        "a b",
        "*",
        "a*",
        "[a-z]",
        "?",
        ":lead",
        "-lead",
        "x" * 201,
        None,
        5,
        "tab\t",
        "new\nline",
    ],
)
def test_bad_channel_names_are_refusals(name):
    with pytest.raises(ChannelNameError):
        check_channel(name)
    with pytest.raises(PermissionError):  # callers treat a bad name like any other refusal
        check_channel(name)


def test_events_default_and_validate():
    assert check_event(None) == "message" and check_event("") == "message"
    assert check_event("typing.start") == "typing.start"
    for bad in ("a b", "a/b", "x" * 101, 5, "-x"):
        with pytest.raises(ChannelNameError):
            check_event(bad)


def test_rooms_split_back_into_environment_and_channel():
    assert split_room(room_name("prod", "chat:lobby")) == ("prod", "chat:lobby")


def test_parse_reads_every_field():
    message = parse(
        json.dumps(
            {
                "type": "subscribe",
                "channel": "c",
                "ref": "1",
                "since": "7",
                "presence": {"n": 1},
                "limit": 5,
                "token": "t",
            }
        ),
        max_bytes=1000,
    )
    assert (
        message.kind,
        message.channel,
        message.ref,
        message.since,
        message.limit,
        message.token,
    ) == ("subscribe", "c", "1", 7, 5, "t")
    assert message.presence == {"n": 1}


def test_parse_is_forgiving_about_optional_fields_and_strict_about_the_frame():
    assert parse('{"type":"ping"}', max_bytes=100).ref is None
    assert parse('{"type":"publish","channel":5}', max_bytes=100).channel == ""
    for raw, code in (
        ("not json", codes.INVALID_JSON),
        ("[1,2]", codes.INVALID_MESSAGE),
        ('{"nope":1}', codes.INVALID_MESSAGE),
        ('{"type":7}', codes.INVALID_MESSAGE),
        ('{"type":"history","limit":"many"}', codes.BAD_REQUEST),
        ('{"type":"history","limit":true}', codes.BAD_REQUEST),
        ('{"type":"ping","ref":[1]}', codes.BAD_REQUEST),
    ):
        with pytest.raises(ProtocolError) as caught:
            parse(raw, max_bytes=100)
        assert caught.value.code == code


def test_parse_enforces_the_size_limit_in_bytes_not_characters():
    raw = json.dumps({"type": "ping", "x": "é" * 60}, ensure_ascii=False)
    assert len(raw) < 100 < len(raw.encode())
    with pytest.raises(ProtocolError) as caught:
        parse(raw, max_bytes=100)
    assert caught.value.code == codes.MESSAGE_TOO_LARGE


def test_long_refs_are_truncated_not_echoed_whole():
    assert len(parse(json.dumps({"type": "ping", "ref": "r" * 500}), max_bytes=1000).ref) == 64
