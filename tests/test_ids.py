import time

import pytest

from pawabase_core.ids import ALPHABET, is_ulid, new_ulid, ulid_timestamp_ms


def test_a_ulid_is_26_crockford_characters():
    value = new_ulid()
    assert len(value) == 26 and set(value) <= set(ALPHABET) and is_ulid(value)


def test_ulids_increase_even_within_one_millisecond():
    batch = [new_ulid() for _ in range(5_000)]
    assert batch == sorted(batch) and len(set(batch)) == len(batch)


def test_a_ulid_carries_its_creation_time():
    before = int(time.time() * 1000)
    value = new_ulid()
    assert before <= ulid_timestamp_ms(value) <= int(time.time() * 1000) + 1


def test_a_clock_that_steps_back_never_repeats_or_decreases():
    first = new_ulid(now_ms=int(time.time() * 1000) + 60_000)
    assert new_ulid(now_ms=1) > first


def test_the_same_millisecond_exhausting_the_random_part_rolls_the_timestamp():
    from pawabase_core import ids

    ids._last_ms, ids._last_random = 5_000_000_000_000, ids._MAX_RANDOM
    rolled = new_ulid(now_ms=5_000_000_000_000)
    assert ulid_timestamp_ms(rolled) == 5_000_000_000_001 and rolled.endswith("0" * 16)


@pytest.mark.parametrize("value", ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "01arz3ndektsv4rrffq69g5fav"])
def test_valid_ulids_in_either_case(value):
    assert is_ulid(value)


@pytest.mark.parametrize(
    "value",
    ["", "1", "01ARZ3NDEKTSV4RRFFQ69G5FA", "01ARZ3NDEKTSV4RRFFQ69G5FAVX", "01ARZ3NDEKTSV4RRFFQ69G5FAU",
     "81ARZ3NDEKTSV4RRFFQ69G5FAV", "0123456789", None, 12],
)  # fmt: skip
def test_malformed_ids_are_refused(value):
    assert not is_ulid(value)


def test_timestamp_of_a_bad_id_raises():
    with pytest.raises(ValueError):
        ulid_timestamp_ms("nope")
