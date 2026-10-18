"""Realtime's own persisted records: channel configuration as real rows (not just the hand-edited JSON blob on
``Environment.settings``), durable messages for channels that ask to keep them, a versioned snapshot per
state-sync channel, device tokens for push, and the durable, auditable rollup billing reads from.

None of this is in the request path of a live publish or subscribe — the hub (``pawabase_server/realtime/``)
stays in-memory/Redis for that. These tables are what survives a restart: configuration, replay, and usage.
"""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from pawabase_server.core.records import ulid_pk
from pawabase_server.db.fields import AnyJSONField


class RealtimeChannelConfig(Model):
    """One channel rule, as a real record Studio can list, create and edit — the DB-backed sibling of the
    hand-edited ``Environment.settings["realtime"]["channels"]`` JSON list and the per-``Resource`` auto-rules.
    ``platform.realtime_settings`` merges all three; this table changes nothing about environments that have
    never used it.
    """

    id = ulid_pk()
    environment = fields.ForeignKeyField(
        "models.Environment", related_name="realtime_channels", on_delete=fields.CASCADE
    )
    #: May use ``*`` and ``{{ auth.* }}`` templates, exactly like a JSON-configured rule (see ``realtime/rules.py``).
    pattern = fields.CharField(max_length=200)
    #: public | private | presence | protected | ephemeral | durable — governs defaults and whether publishes
    #: land in ``RealtimeMessage`` (see ``kind``'s use in ``realtime/rules.py:ChannelRule.durable``).
    kind = fields.CharField(max_length=16, default="public")
    subscribe = fields.CharField(max_length=64, default="authenticated")
    publish = fields.CharField(max_length=64, default="authenticated")
    presence = fields.BooleanField(default=True)
    history = fields.IntField(default=50)
    #: 0 keeps the installation default (``Settings.max_message_bytes``).
    message_size_limit = fields.IntField(default=0)
    #: Publishes per second per connection on this channel, 0 for no extra limit beyond the connection's own.
    publish_rate_limit = fields.IntField(default=0)
    #: Subscribers a channel of this pattern may have at once, 0 for unlimited.
    subscription_limit = fields.IntField(default=0)
    #: Seconds a durable channel's messages are kept; 0 keeps the installation default.
    retention_seconds = fields.IntField(default=0)
    description = fields.CharField(max_length=300, default="")

    class Meta:
        table = "pb_realtime_channel_configs"
        unique_together = (("environment", "pattern"),)
        ordering = ["pattern"]

    def describe(self) -> dict[str, object]:
        return {
            "id": self.id,
            "pattern": self.pattern,
            "kind": self.kind,
            "subscribe": self.subscribe,
            "publish": self.publish,
            "presence": self.presence,
            "history": self.history,
            "message_size_limit": self.message_size_limit,
            "publish_rate_limit": self.publish_rate_limit,
            "subscription_limit": self.subscription_limit,
            "retention_seconds": self.retention_seconds,
            "description": self.description,
        }


class RealtimeMessage(Model):
    """One durably-stored message, for a channel whose rule has ``kind="durable"``. Ephemeral and plain channels
    never write here — their history stays in the hub's in-memory/Redis-backed backlog, bounded by
    ``Settings.history_bytes``, as it always has been. ``history()`` reads from this table first for a durable
    channel and falls back to the backlog only for everything else, so callers see one shape either way.
    """

    id = ulid_pk()
    environment = fields.ForeignKeyField(
        "models.Environment", related_name="realtime_messages", on_delete=fields.CASCADE
    )
    channel = fields.CharField(max_length=200, db_index=True)
    event = fields.CharField(max_length=120)
    data = AnyJSONField(default=dict)
    #: The publisher's own idempotency key, if it sent one. Unique per channel: a retried publish with the same
    #: id is kept once, not duplicated (see ``realtime/service.py:Realtime.publish``).
    client_msg_id = fields.CharField(max_length=128, null=True)
    seq = fields.BigIntField()
    sender = fields.CharField(max_length=128, null=True)
    expires_at = fields.DatetimeField(null=True, db_index=True)

    class Meta:
        table = "pb_realtime_messages"
        unique_together = (("environment", "channel", "client_msg_id"),)
        ordering = ["seq"]

    def describe(self) -> dict[str, object]:
        return {
            "id": self.id,
            "channel": self.channel,
            "event": self.event,
            "data": self.data,
            "client_msg_id": self.client_msg_id,
            "seq": self.seq,
            "sender": self.sender,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RealtimeChannelState(Model):
    """The latest snapshot of a state-sync channel: one row per channel, replaced (never appended) on every
    publish that opts in with ``as_state=True``. A subscriber joins, reads this once, then follows ordinary
    publishes for what changed since — the versioned-snapshot-plus-subsequent-changes shape the brief asks for,
    not a new sync protocol.
    """

    id = ulid_pk()
    environment = fields.ForeignKeyField(
        "models.Environment", related_name="realtime_states", on_delete=fields.CASCADE
    )
    channel = fields.CharField(max_length=200)
    version = fields.BigIntField(default=0)
    snapshot = AnyJSONField(default=dict)

    class Meta:
        table = "pb_realtime_channel_states"
        unique_together = (("environment", "channel"),)

    def describe(self) -> dict[str, object]:
        return {"channel": self.channel, "version": self.version, "snapshot": self.snapshot}


class PushDevice(Model):
    """One device registered for push delivery. ``platform`` names which :mod:`pawabase_server.realtime.push`
    provider sends to it; a device for a platform with no configured provider is kept (so re-enabling later
    needs no re-registration) but is simply skipped on send.
    """

    id = ulid_pk()
    environment = fields.ForeignKeyField(
        "models.Environment", related_name="push_devices", on_delete=fields.CASCADE
    )
    user_id = fields.CharField(max_length=64, null=True, db_index=True)
    #: web | ios | android
    platform = fields.CharField(max_length=16)
    #: The provider's own device handle: a Web Push subscription (endpoint + keys, JSON) or a bare APNs/FCM token.
    token = AnyJSONField()
    last_seen_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "pb_realtime_push_devices"
        unique_together = (("environment", "platform", "user_id"),)

    def describe(self) -> dict[str, object]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "platform": self.platform,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
        }


class RealtimeUsageRecord(Model):
    """One durable, deduplicated usage rollup: one row per environment per hour, upserted from the hub's live
    counters (never read by the request path — see ``realtime/stats.py`` for that). This is the billing-grade
    record the brief asks for; it exists precisely because a live in-memory counter resets on every restart and
    is never a legitimate source for billing on its own.
    """

    id = ulid_pk()
    environment = fields.ForeignKeyField(
        "models.Environment", related_name="realtime_usage_records", on_delete=fields.CASCADE
    )
    #: The start of the hour this row covers, always UTC, always on the hour.
    hour = fields.DatetimeField(db_index=True)
    peak_connections = fields.IntField(default=0)
    connection_minutes = fields.FloatField(default=0)
    messages_published = fields.BigIntField(default=0)
    messages_delivered = fields.BigIntField(default=0)
    bytes_in = fields.BigIntField(default=0)
    bytes_out = fields.BigIntField(default=0)

    class Meta:
        table = "pb_realtime_usage_records"
        unique_together = (("environment", "hour"),)
        ordering = ["-hour"]

    def describe(self) -> dict[str, object]:
        return {
            "hour": self.hour.isoformat() if self.hour else None,
            "peak_connections": self.peak_connections,
            "connection_minutes": self.connection_minutes,
            "messages_published": self.messages_published,
            "messages_delivered": self.messages_delivered,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
        }
