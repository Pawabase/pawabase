import pytest
from sillo.exceptions import HTTPException

from app.env_settings import validate


def test_normalises_platform_keys():
    out = validate(
        {
            "cors_origins": ["https://b.example/", "https://a.example", "https://a.example"],
            "ip_allowlist": ["10.0.0.5", "10.0.0.0/8"],
            "currency": "ngn",
            "timezone": "Africa/Lagos",
            "locale": "en-NG",
            "maintenance": {"enabled": True, "message": " Back soon "},
            "tax_rate": 7.5,
            "old_key": None,
        }
    )
    assert out["cors_origins"] == ["https://a.example", "https://b.example"]
    assert out["ip_allowlist"] == ["10.0.0.0/8", "10.0.0.5/32"]
    assert out["currency"] == "NGN"
    assert out["maintenance"] == {
        "enabled": True,
        "message": "Back soon",
        "retry_after": 300,
        "allow_secret_keys": True,
    }
    assert out["tax_rate"] == 7.5 and out["old_key"] is None


@pytest.mark.parametrize(
    "patch",
    [
        {"cors_origins": ["https://a.example/path"]},
        {"cors_origins": ["*"]},
        {"ip_allowlist": ["not-an-ip"]},
        {"timezone": "Mars/Base"},
        {"currency": "naira"},
        {"locale": "english"},
        {"maintenance": {"retry_after": 0}},
        {"deletion_pending": True},
        {"bad key": 1},
    ],
)
def test_rejects_invalid(patch):
    with pytest.raises(HTTPException) as caught:
        validate(patch)
    assert caught.value.status_code == 422


async def test_patch_validates_and_saves(api):
    from pawabase_core.clients import ServiceError

    env = "/platform/v1/envs/development"
    saved = await api.studio.patch(
        env, json={"settings": {"currency": "ghs", "maintenance": {"enabled": True}}}
    )
    assert saved["settings"]["currency"] == "GHS"
    assert saved["settings"]["maintenance"]["retry_after"] == 300
    with pytest.raises(ServiceError) as err:
        await api.studio.patch(env, json={"settings": {"ip_allowlist": ["nope"]}})
    assert err.value.status == 422 and "ip_allowlist" in str(err.value.body)
    cleared = await api.studio.patch(env, json={"settings": {"currency": None}})
    assert "currency" not in cleared["settings"]
