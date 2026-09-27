"""Trigger blocks: where a run starts.

A trigger's output is the payload that started the run, so the next block can
read ``steps.<trigger>.output`` as well as ``input``. The configuration is read
by the host (the API binds a flow to routes, events, schedules and webhooks from
it); the block itself only hands the input on.
"""

from __future__ import annotations

from ..registry import Block, BlockResult


class _Trigger(Block):
    category = "triggers"
    trigger = True

    async def run(self, config, run):
        return BlockResult(output=run.state["input"])


class HttpTrigger(_Trigger):
    """Runs when an HTTP request reaches a custom route bound to this flow."""

    key = "trigger.http"
    title = "HTTP request"
    config = [
        {
            "name": "method",
            "type": "string",
            "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
            "default": "POST",
        },
        {"name": "path", "type": "string", "description": "Route path, e.g. /orders/{id}/pay"},
        {
            "name": "policy",
            "type": "json",
            "description": "Who may invoke it directly at /flows/v1/<name> (when no path is set)",
        },
    ]


class EventTrigger(_Trigger):
    """Runs when a matching platform event is published."""

    key = "trigger.event"
    title = "Event"
    config = [
        {
            "name": "event",
            "type": "string",
            "required": True,
            "description": "Event name or pattern, e.g. order.* ",
        }
    ]


class ScheduleTrigger(_Trigger):
    """Runs on a schedule managed by the scheduler."""

    key = "trigger.schedule"
    title = "Schedule"
    config = [
        {"name": "cron", "type": "string", "description": "Cron expression, e.g. 0 3 * * *"},
        {"name": "every", "type": "integer", "description": "Or an interval in seconds"},
    ]


class ResourceTrigger(_Trigger):
    """Runs after a resource operation."""

    key = "trigger.resource"
    title = "Resource change"
    config = [
        {"name": "resource", "type": "string", "required": True},
        {
            "name": "operations",
            "type": "array",
            "items": {"type": "string", "enum": ["created", "updated", "deleted"]},
        },
    ]


class WebhookTrigger(_Trigger):
    """Runs when an inbound webhook is received and verified."""

    key = "trigger.webhook"
    title = "Inbound webhook"
    config = [
        {"name": "hook", "type": "string", "required": True, "description": "Inbound webhook slug"}
    ]


class RealtimeTrigger(_Trigger):
    """Runs when a client publishes to a realtime channel."""

    key = "trigger.realtime"
    title = "Realtime message"
    config = [
        {
            "name": "channel",
            "type": "string",
            "required": True,
            "description": "Channel pattern, e.g. chat:*",
        }
    ]


class JobTrigger(_Trigger):
    """Runs when the flow is dispatched as a background job."""

    key = "trigger.job"
    title = "Background job"
    config = [{"name": "queue", "type": "string", "default": "default"}]


class ManualTrigger(_Trigger):
    """Runs when started from Studio or the platform API."""

    key = "trigger.manual"
    title = "Manual run"


BLOCKS = [
    HttpTrigger,
    EventTrigger,
    ScheduleTrigger,
    ResourceTrigger,
    WebhookTrigger,
    RealtimeTrigger,
    JobTrigger,
    ManualTrigger,
]
