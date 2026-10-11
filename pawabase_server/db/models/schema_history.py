"""The record of changes to a resource's table: what was applied, by whom, and how to undo it."""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from pawabase_server.core.records import ulid_pk
from pawabase_server.db.fields import AnyJSONField


class SchemaMigration(Model):
    """One change made to a resource's table in an environment's database."""

    id = ulid_pk()
    env = fields.CharField(max_length=63, db_index=True)
    resource = fields.CharField(max_length=120, db_index=True)
    table_name = fields.CharField(max_length=63)
    #: 1, 2, 3 … per resource in an environment: the order the changes were made in.
    number = fields.IntField()
    #: ``apply`` for a change to the table, ``rollback`` for undoing one.
    kind = fields.CharField(max_length=16, default="apply")
    #: ``applied``, ``failed`` or ``rolled_back`` (an ``apply`` that was undone).
    status = fields.CharField(max_length=16, default="applied")
    #: Where it came from: ``studio``, ``api``, ``blueprint``, ``restore`` or ``rollback``.
    source = fields.CharField(max_length=24, default="studio")
    summary = fields.CharField(max_length=300, default="")
    #: What changed: ``[{"id", "op", "column", "detail", "safe"}]``.
    changes = AnyJSONField(default=list)
    #: The statements that ran, in order, and the ones that undo them, in the order to run.
    up = AnyJSONField(default=list)
    down = AnyJSONField(default=list)
    #: Columns or tables a rollback depends on: they are kept until the migration is purged.
    needs = AnyJSONField(default=list)
    #: The columns the table had once this was applied.
    columns = AnyJSONField(default=list)
    error = fields.TextField(null=True)
    actor = fields.CharField(max_length=120, null=True)
    #: The migration this rolled back, or the rollback that undid this one.
    related_id = fields.CharField(max_length=26, null=True)

    class Meta:
        table = "pb_schema_migrations"
        unique_together = (("env", "resource", "number"),)
        ordering = ["-number"]
