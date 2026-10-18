from tortoise import migrations
from tortoise.migrations import operations as ops
import functools
from json import dumps, loads
from pawabase_server.core.ids import new_ulid
from pawabase_server.db.fields import AnyJSONField
from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise.fields.base import OnDelete
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0029_gateway_instances')]

    initial = False

    operations = [
        ops.CreateModel(
            name='PushDevice',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
                ('environment', fields.ForeignKeyField('models.Environment', source_field='environment_id', db_constraint=True, to_field='id', related_name='push_devices', on_delete=OnDelete.CASCADE)),
                ('user_id', fields.CharField(null=True, db_index=True, max_length=64)),
                ('platform', fields.CharField(description='web | ios | android', max_length=16)),
                ('token', AnyJSONField(description="The provider's own device handle: a Web Push subscription (endpoint + keys, JSON) or a bare APNs/FCM token.", encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('last_seen_at', fields.DatetimeField(auto_now=True, auto_now_add=False)),
            ],
            options={'table': 'pb_realtime_push_devices', 'app': 'models', 'unique_together': (('environment', 'platform', 'user_id'),), 'pk_attr': 'id', 'table_description': 'One device registered for push delivery. ``platform`` names which :mod:`pawabase_server.realtime.push`'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='RealtimeChannelConfig',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
                ('environment', fields.ForeignKeyField('models.Environment', source_field='environment_id', db_constraint=True, to_field='id', related_name='realtime_channels', on_delete=OnDelete.CASCADE)),
                ('pattern', fields.CharField(description='May use ``*`` and ``{{ auth.* }}`` templates, exactly like a JSON-configured rule (see ``realtime/rules.py``).', max_length=200)),
                ('kind', fields.CharField(default='public', description='public | private | presence | protected | ephemeral | durable — governs defaults and whether publishes', max_length=16)),
                ('subscribe', fields.CharField(default='authenticated', max_length=64)),
                ('publish', fields.CharField(default='authenticated', max_length=64)),
                ('presence', fields.BooleanField(default=True)),
                ('history', fields.IntField(default=50)),
                ('message_size_limit', fields.IntField(default=0, description='0 keeps the installation default (``Settings.max_message_bytes``).')),
                ('publish_rate_limit', fields.IntField(default=0, description="Publishes per second per connection on this channel, 0 for no extra limit beyond the connection's own.")),
                ('subscription_limit', fields.IntField(default=0, description='Subscribers a channel of this pattern may have at once, 0 for unlimited.')),
                ('retention_seconds', fields.IntField(default=0, description="Seconds a durable channel's messages are kept; 0 keeps the installation default.")),
                ('description', fields.CharField(default='', max_length=300)),
            ],
            options={'table': 'pb_realtime_channel_configs', 'app': 'models', 'unique_together': (('environment', 'pattern'),), 'pk_attr': 'id', 'table_description': 'One channel rule, as a real record Studio can list, create and edit — the DB-backed sibling of the'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='RealtimeChannelState',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
                ('environment', fields.ForeignKeyField('models.Environment', source_field='environment_id', db_constraint=True, to_field='id', related_name='realtime_states', on_delete=OnDelete.CASCADE)),
                ('channel', fields.CharField(max_length=200)),
                ('version', fields.BigIntField(default=0)),
                ('snapshot', AnyJSONField(default=dict, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
            ],
            options={'table': 'pb_realtime_channel_states', 'app': 'models', 'unique_together': (('environment', 'channel'),), 'pk_attr': 'id', 'table_description': 'The latest snapshot of a state-sync channel: one row per channel, replaced (never appended) on every'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='RealtimeMessage',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
                ('environment', fields.ForeignKeyField('models.Environment', source_field='environment_id', db_constraint=True, to_field='id', related_name='realtime_messages', on_delete=OnDelete.CASCADE)),
                ('channel', fields.CharField(db_index=True, max_length=200)),
                ('event', fields.CharField(max_length=120)),
                ('data', AnyJSONField(default=dict, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('client_msg_id', fields.CharField(null=True, description="The publisher's own idempotency key, if it sent one. Unique per channel: a retried publish with the same", max_length=128)),
                ('seq', fields.BigIntField()),
                ('sender', fields.CharField(null=True, max_length=128)),
                ('expires_at', fields.DatetimeField(null=True, db_index=True, auto_now=False, auto_now_add=False)),
            ],
            options={'table': 'pb_realtime_messages', 'app': 'models', 'unique_together': (('environment', 'channel', 'client_msg_id'),), 'pk_attr': 'id', 'table_description': 'One durably-stored message, for a channel whose rule has ``kind="durable"``. Ephemeral and plain channels'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='RealtimeUsageRecord',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
                ('environment', fields.ForeignKeyField('models.Environment', source_field='environment_id', db_constraint=True, to_field='id', related_name='realtime_usage_records', on_delete=OnDelete.CASCADE)),
                ('hour', fields.DatetimeField(db_index=True, description='The start of the hour this row covers, always UTC, always on the hour.', auto_now=False, auto_now_add=False)),
                ('peak_connections', fields.IntField(default=0)),
                ('connection_minutes', fields.FloatField(default=0)),
                ('messages_published', fields.BigIntField(default=0)),
                ('messages_delivered', fields.BigIntField(default=0)),
                ('bytes_in', fields.BigIntField(default=0)),
                ('bytes_out', fields.BigIntField(default=0)),
            ],
            options={'table': 'pb_realtime_usage_records', 'app': 'models', 'unique_together': (('environment', 'hour'),), 'pk_attr': 'id', 'table_description': "One durable, deduplicated usage rollup: one row per environment per hour, upserted from the hub's live"},
            bases=['Model'],
        ),
        ops.AlterField(
            model_name='Bucket',
            name='cache_max_age',
            field=fields.IntField(default=0, description='``Cache-Control: max-age`` for objects served from the gateway. 0 revalidates every time.'),
        ),
        ops.AlterField(
            model_name='Bucket',
            name='list_policy',
            field=AnyJSONField(null=True, description='Who may list, replace and delete. Unset falls back to what an older bucket had:', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads),
        ),
        ops.AlterField(
            model_name='Bucket',
            name='transforms',
            field=AnyJSONField(default=dict, description='Delivery transformations (see ``infra/storage/transform.py``); ``{}`` is the defaults.', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads),
        ),
        ops.AlterField(
            model_name='EventLog',
            name='routed_at',
            field=fields.DatetimeField(null=True, description='Set once every consumer has been handed its work. A row without it is resumed by a retry, skipping consumers already served.', auto_now=False, auto_now_add=False),
        ),
        ops.AlterModelOptions(
            name='FirewallBan',
            options={'table': 'pb_firewall_bans', 'app': 'models', 'pk_attr': 'id', 'table_description': 'An address (or range) refused until ``expires_at`` (or until removed, when it has none).'},
        ),
        ops.AlterField(
            model_name='FirewallBan',
            name='source',
            field=fields.CharField(default='manual', description='``manual``, ``auto`` (too many errors) or ``rate_limit``.', max_length=16),
        ),
        ops.AlterField(
            model_name='FirewallConfig',
            name='auto_ban',
            field=AnyJSONField(default=dict, description='``{enabled, threshold, window_seconds, ban_seconds, statuses, exempt}``.', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads),
        ),
        ops.AlterField(
            model_name='FirewallConfig',
            name='managed',
            field=AnyJSONField(default=dict, description='``{set: "off" | "log" | "block"}`` (``core/firewall.py`` ``MANAGED``).', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads),
        ),
        ops.AlterField(
            model_name='FirewallEvent',
            name='action',
            field=fields.CharField(description='``block``, ``log``, ``rate_limit``, ``ban`` or ``allow``.', max_length=16),
        ),
        ops.AlterField(
            model_name='FirewallEvent',
            name='source',
            field=fields.CharField(description='``rule``, ``managed``, ``ban`` or ``rate_limit``.', max_length=16),
        ),
        ops.AlterModelOptions(
            name='FirewallRule',
            options={'table': 'pb_firewall_rules', 'app': 'models', 'pk_attr': 'id', 'table_description': 'An ordered rule the gateway checks before a request reaches anything (see ``core/firewall.py``).'},
        ),
        ops.AlterField(
            model_name='FirewallRule',
            name='action',
            field=fields.CharField(default='block', description='``allow``, ``block``, ``log`` or ``rate_limit``.', max_length=16),
        ),
        ops.AlterField(
            model_name='FirewallRule',
            name='conditions',
            field=AnyJSONField(default=list, description='``[{field, op, value}]``, joined by ``logic`` (``all`` or ``any``).', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads),
        ),
        ops.AlterField(
            model_name='FirewallRule',
            name='expires_at',
            field=fields.DatetimeField(null=True, description='A temporary rule stops applying at this time.', auto_now=False, auto_now_add=False),
        ),
        ops.AlterField(
            model_name='FirewallRule',
            name='match',
            field=AnyJSONField(default=dict, description='The older shape: any of ``ips``, ``paths``, ``methods``, ``user_agents``. Combined with ``conditions``.', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads),
        ),
        ops.AlterField(
            model_name='FirewallRule',
            name='mode',
            field=fields.CharField(default='enforce', description='``enforce``, or ``simulate``: evaluated and recorded, never acted on.', max_length=10),
        ),
        ops.AlterField(
            model_name='FirewallRule',
            name='rate_limit',
            field=AnyJSONField(null=True, description='For ``rate_limit``: ``{requests, window_seconds, by, ban_seconds}``.', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads),
        ),
        ops.AlterField(
            model_name='FunctionRun',
            name='kind',
            field=fields.CharField(null=True, description='``call`` (an fn() function) or ``http`` (an HTTP function); ``None`` on runs recorded before HTTP functions existed.', max_length=8),
        ),
        ops.AlterField(
            model_name='FunctionRun',
            name='limit_hit',
            field=fields.CharField(null=True, description='Which limit stopped it, when one did: ``timeout``, ``memory``, ``cpu``, ``concurrency``, ``max_body``.', max_length=16),
        ),
        ops.AlterField(
            model_name='FunctionRun',
            name='limits',
            field=AnyJSONField(null=True, description='The limits this run had (``functions/limits.py``), and what it used of them.', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads),
        ),
        ops.AlterModelOptions(
            name='GatewayInstance',
            options={'table': 'pb_gateway_instances', 'app': 'models', 'pk_attr': 'id', 'table_description': 'One gateway replica of this installation.'},
        ),
        ops.AlterField(
            model_name='GatewayInstance',
            name='drain_requested_at',
            field=fields.DatetimeField(null=True, description='Set once, at registration; read by the reconciler to prune a replica that asked to drain and never came back.', auto_now=False, auto_now_add=False),
        ),
        ops.AlterField(
            model_name='GatewayInstance',
            name='draining',
            field=fields.BooleanField(default=False, description="True once this instance has asked to stop taking new traffic; a load balancer's health check and Studio both read this."),
        ),
        ops.AlterField(
            model_name='GatewayInstance',
            name='host',
            field=fields.CharField(description='Where a load balancer or Studio reaches its public port (host:port, or a container/service name Traefik resolves).', max_length=255),
        ),
        ops.AlterField(
            model_name='GatewayInstance',
            name='id',
            field=fields.CharField(primary_key=True, unique=True, db_index=True, description='Stable across restarts only if the operator sets ``PAWABASE_GATEWAY_INSTANCE_ID``; otherwise a fresh id per process start,', max_length=128),
        ),
        ops.AlterField(
            model_name='GatewayInstance',
            name='internal_url',
            field=fields.CharField(description="Its internal listener, for Studio's per-instance calls (telemetry, functions-runtime status).", max_length=255),
        ),
        ops.AlterField(
            model_name='GatewayInstance',
            name='plane_id',
            field=fields.CharField(default='', db_index=True, description='Which API plane placed it, for an installation with more than one (empty for a plain self-hosted single-host deployment).', max_length=64),
        ),
        ops.AlterField(
            model_name='GatewayInstance',
            name='version',
            field=fields.CharField(default='', description='The running build/version string, for staged rollouts and the Studio fleet view.', max_length=64),
        ),
        ops.AlterModelOptions(
            name='Lease',
            options={'table': 'pb_leases', 'app': 'models', 'pk_attr': 'name', 'table_description': "A named lease one process holds at a time (the scheduler's leadership), renewed before ``expires_at``."},
        ),
        ops.AlterField(
            model_name='ObjectShare',
            name='token_ciphertext',
            field=fields.TextField(default='', description='The token sealed with the platform key, so Studio can show the link again to the person who manages it. Lookup uses the hash.', unique=False),
        ),
        ops.AlterField(
            model_name='Project',
            name='limits',
            field=AnyJSONField(default=dict, description='Versioned gateway entitlement, carried by the API-key cache rather than read per request.', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads),
        ),
        ops.AlterField(
            model_name='Schedule',
            name='catch_up',
            field=fields.BooleanField(default=False, description='Fire once on start when a cron slot was missed while no scheduler was running.'),
        ),
        ops.AlterField(
            model_name='Schedule',
            name='overlap',
            field=fields.CharField(default='allow', description='``allow``: fire even while the previous run is still queued or running. ``skip``: skip that firing.', max_length=8),
        ),
        ops.AlterField(
            model_name='Schedule',
            name='timezone',
            field=fields.CharField(null=True, description='IANA zone a cron expression is read in (``Africa/Lagos``); empty is UTC.', max_length=64),
        ),
        ops.AlterModelOptions(
            name='UsageCounter',
            options={'table': 'pb_usage_counters', 'app': 'models', 'unique_together': (('project_id', 'metric', 'period'),), 'pk_attr': 'id', 'table_description': "A project's metered usage, where counting the source on every request would cost too much (see ``projects.quotas``)."},
        ),
        ops.AlterField(
            model_name='WebhookDelivery',
            name='next_attempt_at',
            field=fields.DatetimeField(null=True, description='When the next attempt is scheduled, while the delivery is retrying.', auto_now=False, auto_now_add=False),
        ),
    ]
