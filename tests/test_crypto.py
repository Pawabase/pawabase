import pytest
from cryptography.fernet import InvalidToken

from pawabase_core.crypto import BOUND_PREFIX, SecretBox

MASTER = "a-master-key-of-reasonable-length-0123456789"


def test_without_an_environment_nothing_changes():
    box = SecretBox(MASTER)
    sealed = box.seal("hunter2")
    assert not sealed.startswith(BOUND_PREFIX) and not box.is_bound(sealed)
    assert box.open(sealed) == "hunter2"
    # and it opens whatever environment asks, as values sealed before environments had keys do
    assert box.open(sealed, "production") == "hunter2"


def test_a_sealed_value_opens_only_for_its_environment():
    box = SecretBox(MASTER)
    sealed = box.seal("hunter2", "production")
    assert sealed.startswith(BOUND_PREFIX) and box.is_bound(sealed)
    assert box.open(sealed, "production") == "hunter2"
    with pytest.raises(InvalidToken):
        box.open(sealed, "staging")


def test_a_bound_value_needs_to_be_told_its_environment():
    box = SecretBox(MASTER)
    with pytest.raises(ValueError, match="sealed for an environment"):
        box.open(box.seal("x", "production"))


def test_a_different_master_key_opens_nothing():
    sealed = SecretBox(MASTER).seal("hunter2", "production")
    with pytest.raises(InvalidToken):
        SecretBox("another-master-key-of-reasonable-length").open(sealed, "production")


def test_each_environment_has_its_own_key():
    box = SecretBox(MASTER)
    a, b = box.seal("same", "alpha"), box.seal("same", "beta")
    assert a != b and box.open(a, "alpha") == box.open(b, "beta") == "same"
    # names that differ only by case or punctuation are different environments here
    with pytest.raises(InvalidToken):
        box.open(box.seal("x", "my-env"), "my_env")


def test_resealing_moves_a_value_to_another_environment():
    box = SecretBox(MASTER)
    moved = box.reseal(box.seal("whsec_abc", "production"), source="production", target="staging")
    assert box.open(moved, "staging") == "whsec_abc"
    with pytest.raises(InvalidToken):
        box.open(moved, "production")
    legacy = box.reseal(box.seal("old-style"), source="production", target="staging")
    assert box.is_bound(legacy) and box.open(legacy, "staging") == "old-style"
