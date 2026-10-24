"""What a request used to look up every time is looked up once and trusted for a moment, and a change still shows at once."""

import pytest

from pawabase_server.core import tokens
from pawabase_server.db.models import Environment


async def test_a_loaded_environment_is_not_asked_for_again_within_its_interval(api, monkeypatch):
    envs = api.platform.envs
    state = await envs.get("development")
    api.platform.settings.env_state_ttl = 30.0
    asked = []
    original = Environment.filter

    def spy(*args, **kwargs):
        asked.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(Environment, "filter", spy)
    envs._checked["development"] = tokens.time.monotonic()  # a check a moment ago
    assert await envs.get("development") is state
    assert asked == []  # served from memory


async def test_a_change_made_here_is_seen_by_the_next_request(api):
    api.platform.settings.env_state_ttl = 30.0
    envs = api.platform.envs
    state = await envs.get("development")
    envs._checked["development"] = tokens.time.monotonic()
    environment = await Environment.get(name="development")
    from pawabase_server.environments.state import bump

    await bump(environment.id)
    reloaded = await envs.get("development")
    assert reloaded is not state and reloaded.version == state.version + 1


async def test_a_ttl_of_zero_asks_every_time(api, monkeypatch):
    api.platform.settings.env_state_ttl = 0
    envs = api.platform.envs
    await envs.get("development")
    asked = []
    original = Environment.filter
    monkeypatch.setattr(
        Environment, "filter", lambda *a, **k: (asked.append(k), original(*a, **k))[1]
    )
    await envs.get("development")
    assert asked


def test_a_verified_token_is_not_verified_twice_and_never_outlives_itself(monkeypatch):
    tokens._verified.clear()
    token = tokens.issue_user_token(
        "master-secret-for-the-test-0123456789",
        env="development",
        user_id="u1",
        jti="j1",
        session_id="s1",
        ttl=300,
    )
    calls = []
    real = tokens._decode
    monkeypatch.setattr(tokens, "_decode", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    first = tokens.verify_user_token(
        token, "master-secret-for-the-test-0123456789", env="development"
    )
    second = tokens.verify_user_token(
        token, "master-secret-for-the-test-0123456789", env="development"
    )
    assert first == second and len(calls) == 1
    second["sub"] = "tampered"  # the caller's copy is its own
    assert (
        tokens.verify_user_token(token, "master-secret-for-the-test-0123456789", env="development")[
            "sub"
        ]
        == "u1"
    )
    with pytest.raises(tokens.TokenInvalid):  # another environment's secret does not match
        tokens.verify_user_token(token, "master-secret-for-the-test-0123456789", env="other")
