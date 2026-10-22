"""Mail in Studio: template drafts, publishing and versions; composing, scheduling and retrying; previews; the brand kit."""

import base64
from datetime import UTC, datetime, timedelta

import pytest

from pawabase_server.core.clients import ServiceError

ENV = "/platform/v1/envs/development"


async def _template(api, name="welcome", **fields):
    body = {
        "name": name,
        "subject": "Hi {{ user.name }}",
        "html": "<p>Hello {{ user.name }}</p>",
        "text": "Hello {{ user.name }}",
        **fields,
    }
    return await api.studio.post(f"{ENV}/mail-templates", json=body)


async def test_a_template_keeps_its_editor_fields(api):
    created = await _template(
        api,
        category="onboarding",
        tags=["users"],
        folder="Auth",
        favorite=True,
        preheader="Glad you are here",
        design={"doc": {"type": "doc", "content": []}},
        variables=[{"name": "user.name", "sample": "Ada"}],
    )
    assert created["category"] == "onboarding"
    assert created["tags"] == ["users"]
    assert created["favorite"] is True
    assert created["design"] == {"doc": {"type": "doc", "content": []}}
    assert created["variables"][0]["sample"] == "Ada"


async def test_a_draft_is_checked_for_template_syntax(api):
    with pytest.raises(ServiceError) as caught:
        await _template(api, draft={"subject": "{{ broken", "html": "<p>x</p>"})
    assert "draft subject" in str(caught.value.body)


async def test_publishing_turns_the_draft_into_a_numbered_version(api):
    await _template(
        api,
        status="draft",
        html="",
        text="",
        subject="",
        draft={"subject": "Welcome {{ user.name }}", "html": "<h1>v1</h1>", "text": "v1"},
    )
    with pytest.raises(ServiceError):
        # a never-published template cannot be sent
        await api.studio.post(
            f"{ENV}/mail/test", json={"to": ["a@example.com"], "template": "welcome"}
        )
    first = await api.studio.post(f"{ENV}/mail-templates/welcome/publish", json={"note": "first"})
    assert first["version"] == 1 and first["status"] == "published" and first["draft"] is None
    assert first["html"] == "<h1>v1</h1>"

    await api.studio.put(
        f"{ENV}/mail-templates/welcome",
        json={**first, "draft": {"subject": "Welcome", "html": "<h1>v2</h1>", "text": "v2"}},
    )
    second = await api.studio.post(f"{ENV}/mail-templates/welcome/publish", json={})
    assert second["version"] == 2 and second["html"] == "<h1>v2</h1>"

    versions = (await api.studio.get(f"{ENV}/mail-templates/welcome/versions"))["data"]
    assert [v["number"] for v in versions] == [2, 1]
    assert versions[1]["note"] == "first"

    restored = await api.studio.post(f"{ENV}/mail-templates/welcome/versions/1/restore")
    assert restored["draft"]["html"] == "<h1>v1</h1>"
    assert restored["html"] == "<h1>v2</h1>"  # still live until published


async def test_renaming_keeps_versions_and_names_flows_that_send_it(api):
    await _template(api)
    await api.studio.post(f"{ENV}/mail-templates/welcome/publish", json={})
    from pawabase_server.db.models import Environment, Flow

    environment = await Environment.get(name="development")
    await Flow.create(
        environment=environment,
        name="onboard",
        definition={
            "nodes": [{"id": "n1", "type": "email", "data": {"template": "welcome"}}],
            "edges": [],
        },
    )
    renamed = await api.studio.post(f"{ENV}/mail-templates/welcome/rename", json={"to": "hello"})
    assert renamed["name"] == "hello"
    assert renamed["flows_to_update"] == ["onboard"]
    assert len((await api.studio.get(f"{ENV}/mail-templates/hello/versions"))["data"]) == 1


async def test_render_previews_with_sample_data(api):
    out = await api.studio.post(
        f"{ENV}/mail/render",
        json={
            "subject": "Hi {{ name }}",
            "html": "{% if vip %}<b>VIP</b>{% endif %}<p>{{ name }}</p>",
            "data": {"name": "Ada", "vip": True},
        },
    )
    assert out["subject"] == "Hi Ada"
    assert out["html"] == "<b>VIP</b><p>Ada</p>"


async def test_composed_mail_is_logged_with_its_content_and_attachments(api):
    sent = await api.studio.post(
        f"{ENV}/mail/send",
        json={
            "to": ["ada@example.com"],
            "subject": "Invoice",
            "html": "<p>Attached</p>",
            "text": "Attached",
            "from_email": "Billing <billing@example.com>",
            "reply_to": "help@example.com",
            "attachments": [
                {
                    "filename": "invoice.txt",
                    "content_type": "text/plain",
                    "content": base64.b64encode(b"total 10").decode(),
                }
            ],
        },
    )
    assert sent["suppressed"] is True  # no mail host in tests
    rows = (await api.studio.get(f"{ENV}/mail/log", params={"q": "invoice"}))["data"]
    assert [r["id"] for r in rows] == [sent["id"]]
    assert rows[0]["attachments"] == [
        {"filename": "invoice.txt", "content_type": "text/plain", "size": 8}
    ]
    assert "html" not in rows[0]
    detail = await api.studio.get(f"{ENV}/mail/log/{sent['id']}")
    assert (
        detail["html"] == "<p>Attached</p>"
        and detail["from_email"] == "Billing <billing@example.com>"
    )
    assert "content" not in detail["attachments"][0]

    retried = await api.studio.post(f"{ENV}/mail/log/{sent['id']}/retry")
    detail = await api.studio.get(f"{ENV}/mail/log/{sent['id']}")
    assert [r["id"] for r in detail["retries"]] == [retried["id"]]


async def test_scheduled_mail_waits_then_is_sent_once(api):
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    scheduled = await api.studio.post(
        f"{ENV}/mail/send",
        json={"to": ["a@example.com"], "subject": "Later", "text": "hi", "scheduled_at": later},
    )
    assert scheduled["scheduled"] is True
    assert await api.platform.mail.send_due() == 0

    from pawabase_server.db.models import MailLog

    await MailLog.filter(id=scheduled["id"]).update(
        scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    assert await api.platform.mail.send_due() == 1
    assert await api.platform.mail.send_due() == 0
    row = await MailLog.get(id=scheduled["id"])
    assert row.status == "suppressed" and row.attempts == 1


async def test_a_scheduled_message_can_be_canceled(api):
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    scheduled = await api.studio.post(
        f"{ENV}/mail/send",
        json={"to": ["a@example.com"], "subject": "Later", "text": "hi", "scheduled_at": later},
    )
    await api.studio.post(f"{ENV}/mail/log/{scheduled['id']}/cancel")
    with pytest.raises(ServiceError):
        await api.studio.post(f"{ENV}/mail/log/{scheduled['id']}/cancel")


async def test_stats_and_usage_count_sends_per_template(api):
    await _template(api)
    await api.studio.post(
        f"{ENV}/mail/test",
        json={"to": ["a@example.com"], "template": "welcome", "data": {"user": {"name": "Ada"}}},
    )
    stats = await api.studio.get(f"{ENV}/mail/stats", params={"days": 7})
    assert stats["total"] == 1 and stats["by_status"] == {"suppressed": 1}
    assert len(stats["series"]) == 7
    usage = (await api.studio.get(f"{ENV}/mail/templates/usage"))["data"]
    assert usage["welcome"]["sent"] == 1


async def test_the_brand_kit_round_trips(api):
    empty = await api.studio.get(f"{ENV}/mail/kit")
    assert empty["blocks"] == []
    saved = await api.studio.put(
        f"{ENV}/mail/kit",
        json={
            "brand": {"accent": "#ff7049"},
            "palettes": [{"name": "Brand", "colors": ["#ff7049"]}],
            "blocks": [{"id": "b1", "name": "Signature", "kind": "signature", "content": []}],
            "folders": ["Auth"],
        },
    )
    assert saved["brand"]["accent"] == "#ff7049"
    assert (await api.studio.get(f"{ENV}/mail/kit"))["folders"] == ["Auth"]


async def test_connection_test_reports_a_refused_server(api):
    out = await api.studio.post(f"{ENV}/mail/connection", json={"host": "127.0.0.1", "port": 1})
    assert out["ok"] is False and out["error"]
