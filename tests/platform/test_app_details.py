"""App details in Settings: validated, used as the default values of mail templates, and as the reply-to address."""

from datetime import UTC, datetime, timedelta

import pytest

from pawabase_server.core.clients import ServiceError
from pawabase_server.db.models import RequestLog

ENV = "/platform/v1/envs/development"


async def _set_app(api, **details):
    return await api.studio.patch(ENV, json={"settings": {"app": details}})


async def _template(api):
    await api.studio.post(
        f"{ENV}/mail-templates",
        json={
            "name": "hello",
            "subject": "Welcome to {{ app.name }}",
            "html": "<p>Hi from {{ app.name }}</p>",
            "text": "Hi from {{ app.name }}",
        },
    )
    await api.studio.post(f"{ENV}/mail-templates/hello/publish", json={})


async def _last_mail(api):
    rows = (await api.studio.get(f"{ENV}/mail/log"))["data"]
    return await api.studio.get(f"{ENV}/mail/log/{rows[0]['id']}")


async def test_app_details_are_saved_and_checked(api):
    saved = await _set_app(
        api, name="Northwind", support_email="help@northwind.io", website="https://northwind.io"
    )
    assert saved["settings"]["app"]["name"] == "Northwind"
    with pytest.raises(ServiceError) as caught:
        await _set_app(api, support_email="not-an-email")
    assert caught.value.status == 422
    with pytest.raises(ServiceError) as caught:
        await _set_app(api, website="northwind")
    assert caught.value.status == 422


async def test_templates_read_the_app_details_without_being_given_them(api):
    await _set_app(api, name="Northwind", support_email="help@northwind.io")
    await _template(api)
    await api.studio.post(
        f"{ENV}/mail/test", json={"to": ["a@example.com"], "template": "hello", "subject": ""}
    )
    mail = await _last_mail(api)
    assert mail["subject"] == "Welcome to Northwind"
    assert "Hi from Northwind" in mail["html"]
    assert mail["reply_to"] == "help@northwind.io"


async def test_what_the_caller_gives_wins_over_the_settings(api):
    await _set_app(api, name="Northwind", support_email="help@northwind.io")
    await _template(api)
    await api.studio.post(
        f"{ENV}/mail/test",
        json={
            "to": ["a@example.com"],
            "template": "hello",
            "subject": "",
            "data": {"app": {"name": "Contoso"}},
        },
    )
    assert (await _last_mail(api))["subject"] == "Welcome to Contoso"


async def test_requests_can_be_limited_to_a_time_window(api):
    now = datetime.now(UTC)
    for name, age in (("recent", 5), ("old", 600)):
        await RequestLog.create(
            request_id=name,
            service="api",
            env="development",
            method="GET",
            path=f"/rest/v1/{name}",
            status=200,
            started_at=(now - timedelta(minutes=age)).isoformat(),
        )
    everything = (await api.studio.get(f"{ENV}/requests"))["data"]
    assert {r["request_id"] for r in everything} >= {"recent", "old"}
    last_hour = (await api.studio.get(f"{ENV}/requests", params={"minutes": 60}))["data"]
    ids = {r["request_id"] for r in last_hour}
    assert "recent" in ids and "old" not in ids
    with pytest.raises(ServiceError) as caught:
        await api.studio.get(f"{ENV}/requests", params={"minutes": "soon"})
    assert caught.value.status == 400
