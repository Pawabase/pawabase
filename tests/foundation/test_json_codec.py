"""JSON columns hold documents: JSON text sent for one is stored as the document, not wrapped again."""

import json

from pawabase_server.data.sql import decode_value, encode_value

SPEC = {"type": "json"}
FEATURES = ["1 project", "Community support"]


def stored(value):
    return encode_value("postgres", SPEC, value)


def test_a_document_is_stored_once():
    assert json.loads(stored(FEATURES)) == FEATURES


def test_json_text_for_a_document_is_stored_as_the_document():
    assert json.loads(stored(json.dumps(FEATURES))) == FEATURES
    assert json.loads(stored(json.dumps(json.dumps(FEATURES)))) == FEATURES


def test_other_strings_stay_strings():
    assert json.loads(stored("hello")) == "hello"
    assert json.loads(stored("42")) == "42"
    assert json.loads(stored("[not json")) == "[not json"


def test_a_row_wrapped_by_the_old_behaviour_reads_as_the_document():
    wrapped = json.dumps(json.dumps(json.dumps(FEATURES)))
    assert decode_value(SPEC, wrapped) == FEATURES
    assert decode_value(SPEC, json.dumps(FEATURES)) == FEATURES
    assert decode_value(SPEC, json.dumps("plain")) == "plain"
