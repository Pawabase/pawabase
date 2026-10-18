"""A dashboard: a page (or several) of widgets — metrics, tables, kanban boards, forms — bound to this
environment's own resources and functions. This record only describes layout and bindings; every read and
write a published dashboard makes goes through the normal ``/rest/v1`` API under its own publishable key, so
each resource's existing policies decide what a viewer can see or change. Nothing here bypasses that.
"""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from pawabase_server.core.records import ulid_pk
from pawabase_server.db.fields import AnyJSONField


class Dashboard(Model):
    """A page (or several) of widgets, published as its own web page.

    Attributes:
    name: URL slug, unique per environment — the dashboard is published at ``/dashboards/v1/<env>/<name>``.
    definition: ``{"pages": [{"id", "name", "path", "widgets": [...]}]}`` — see ``pawabase_server.dashboards.engine``.
    public: Anyone may load the published page. Off keeps it configured but not servable (not yet signed-in
        gating — a later, separate feature).
    publishable_key_ciphertext: The environment's own publishable key, sealed with ``platform.box``, embedded
        in the rendered page so its client-side code can call the REST API as any browser app would. Pasted
        once when publishing, like a webhook's signing secret; never returned once set.
    """

    id = ulid_pk()
    environment = fields.ForeignKeyField(
        "models.Environment", related_name="dashboards", on_delete=fields.CASCADE
    )
    name = fields.CharField(max_length=64)
    title = fields.CharField(max_length=160, default="")
    description = fields.TextField(default="")
    definition = AnyJSONField(default=dict)
    enabled = fields.BooleanField(default=True)
    public = fields.BooleanField(default=False)
    publishable_key_ciphertext = fields.TextField(null=True)

    class Meta:
        table = "pb_dashboards"
        unique_together = (("environment", "name"),)
        ordering = ["name"]

    def describe(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "definition": self.definition,
            "enabled": self.enabled,
            "public": self.public,
            "published": bool(self.publishable_key_ciphertext),
        }
