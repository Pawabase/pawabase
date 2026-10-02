"""The event processor: every platform event, recorded and routed.

It consumes the platform event bus (Sillo events: a durable Redis backlog in
production, in-process in development). For each event it:

1. records it, with its origin, for Studio's event inspector;
2. finds its consumers: subscriptions, flows whose ``trigger.event`` or
   ``trigger.resource`` matches, and outbound webhooks;
3. hands each consumer its work: a queued flow or function job, a realtime
   publication, or a queued webhook delivery;
4. records who consumed it and how that went.

Consumers are queued rather than run inline, so a slow consumer never delays
the others and each one retries independently.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from sillo.exceptions import HTTPException

from app.platform import Platform
from app.runtime import matches_condition
from app.state import EnvironmentState
from app.webhooks import endpoint_matches, queue_deliveries
from database.models import EventLog
from pawabase_core.events import PlatformEvent
from pawabase_core.templating import render

logger = logging.getLogger("pawabase.events")


def flow_event_entries(state: EnvironmentState, event: PlatformEvent) -> list[tuple[str, str]]:
    """``(flow, trigger node)`` pairs whose event or resource trigger matches."""
    matches = []
    for flow in state.flows.values():
        if not flow.enabled:
            continue
        for node in flow.definition.get("nodes", []):
            data = node.get("data") or {}
            block = data.get("block")
            config = data.get("config") or {}
            if (
                block == "trigger.event"
                and config.get("event")
                and fnmatch.fnmatchcase(event.name, config["event"])
            ):
                matches.append((flow.name, node["id"]))
            elif (
                block == "trigger.realtime"
                and config.get("channel")
                and event.name == "realtime.message"
            ):
                channel = str((event.payload or {}).get("channel", ""))
                if fnmatch.fnmatchcase(channel, config["channel"]):
                    matches.append((flow.name, node["id"]))
            elif block == "trigger.resource" and config.get("resource"):
                resource, _, change = event.name.rpartition(".")
                operations = config.get("operations") or ["created", "updated", "deleted"]
                if resource == config["resource"] and change in operations:
                    matches.append((flow.name, node["id"]))
    return matches


class EventProcessor:
    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.processed = 0

    def attach(self) -> EventProcessor:
        self.platform.bus.subscribe(self.handle)
        return self

    async def handle(self, event: PlatformEvent) -> list[dict[str, Any]]:
        try:
            state = (
                await self.platform.state_for_release(event.project, event.env, event.release_id)
                if event.release_id
                else await self.platform.state(event.project, event.env)
            )
        except HTTPException:
            logger.warning(
                "event %s for unknown environment %s/%s", event.name, event.project, event.env
            )
            return []
        if await EventLog.filter(event_id=event.id).exists():
            return []  # at-least-once delivery: this one was already handled
        log = await EventLog.create(
            event_id=event.id,
            project=event.project,
            env=event.env,
            name=event.name,
            source=event.source,
            actor=event.actor,
            payload=event.payload,
            request_id=event.request_id,
            occurred_at=event.occurred_at,
        )
        consumers = await self.route(state, event)
        log.consumers = consumers
        await log.save(update_fields=["consumers"])
        self.processed += 1
        return consumers

    async def route(self, state: EnvironmentState, event: PlatformEvent) -> list[dict[str, Any]]:
        from app.jobs.flows import RunFlowJob
        from app.jobs.functions import RunFunctionJob

        platform = self.platform
        consumers: list[dict[str, Any]] = []
        event_input = {"event": event.to_dict()}
        condition_context = {"event": event.to_dict()}

        async def attempt(kind: str, target: str, work) -> None:
            entry: dict[str, Any] = {"type": kind, "target": target}
            try:
                result = await work()
                entry["status"] = "queued" if kind in ("flow", "function", "webhook") else "done"
                if result is not None:
                    entry["ref"] = result
            except Exception as exc:
                entry["status"] = "failed"
                entry["error"] = f"{type(exc).__name__}: {exc}"
            consumers.append(entry)

        for subscription in state.subscriptions:
            if not fnmatch.fnmatchcase(event.name, subscription.event):
                continue
            if not matches_condition(subscription.condition, condition_context):
                continue
            if subscription.target_type == "flow":
                await attempt(
                    "flow",
                    subscription.target,
                    lambda s=subscription: platform.dispatch(
                        RunFlowJob,
                        project=state.project_ref,
                        env=state.env_name,
                        target=s.target,
                        source="event",
                        flow=s.target,
                        input=event_input,
                        trigger="event",
                        auth={"authenticated": False, "kind": "system"},
                        request_id=event.request_id,
                        release_id=state.release_id,
                        api_version=state.api_version,
                    ),
                )
            elif subscription.target_type == "function":
                await attempt(
                    "function",
                    subscription.target,
                    lambda s=subscription: platform.dispatch(
                        RunFunctionJob,
                        project=state.project_ref,
                        env=state.env_name,
                        target=s.target,
                        source="event",
                        function=s.target,
                        input=event_input,
                        trigger="event",
                        auth={"authenticated": False, "kind": "system"},
                        request_id=event.request_id,
                        release_id=state.release_id,
                        api_version=state.api_version,
                    ),
                )
            elif subscription.target_type == "realtime":
                channel = str(
                    render(subscription.target or f"events:{event.name}", condition_context)
                )
                await attempt(
                    "realtime",
                    channel,
                    lambda c=channel: platform.publish(state, c, event.name, event.payload),
                )

        for flow_name, node_id in flow_event_entries(state, event):
            await attempt(
                "flow",
                flow_name,
                lambda f=flow_name, n=node_id: platform.dispatch(
                    RunFlowJob,
                    project=state.project_ref,
                    env=state.env_name,
                    target=f,
                    source="event",
                    flow=f,
                    input=event_input,
                    trigger="event",
                    entry=n,
                    auth={"authenticated": False, "kind": "system"},
                    request_id=event.request_id,
                    release_id=state.release_id,
                    api_version=state.api_version,
                ),
            )

        if any(
            endpoint.enabled and endpoint_matches(endpoint.events, event.name)
            for endpoint in state.webhooks
        ):
            await attempt(
                "webhook",
                "endpoints",
                lambda: queue_deliveries(
                    platform, state, event_id=event.id, event=event.name, payload=event.payload
                ),
            )
        return consumers
