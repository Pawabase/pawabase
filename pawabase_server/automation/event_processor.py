"""The event processor: every platform event, recorded and routed.

Every event reaches it as a :class:`ProcessEventJob` on the ``events`` job
queue, so an event survives a worker crash and a failed routing is retried
with backoff. For each event it:

1. records it, with its origin, for Studio's event inspector;
2. finds its consumers: subscriptions, flows whose ``trigger.event`` or
   ``trigger.resource`` matches, and outbound webhooks;
3. hands each consumer its work: a queued flow or function job, a realtime
   publication, or a queued webhook delivery;
4. records who consumed it and how that went.

Consumers are queued rather than run inline, so a slow consumer never delays
the others and each one retries independently. Routing is resumable: the log
remembers which consumers were already handed their work, a retry serves only
the rest, and ``routed_at`` marks an event that reached all of them.
"""

from __future__ import annotations

import fnmatch
import logging
from datetime import UTC, datetime
from typing import Any

from sillo.exceptions import HTTPException
from tortoise.exceptions import IntegrityError

from pawabase_server.automation.webhooks import endpoint_matches, queue_deliveries
from pawabase_server.core.events import PlatformEvent
from pawabase_server.core.templating import render
from pawabase_server.db.models import EventLog
from pawabase_server.environments.state import EnvironmentState
from pawabase_server.functions.runtime import matches_condition
from pawabase_server.platform import Platform

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


class EventRoutingFailed(RuntimeError):
    """Some consumer of an event could not be handed its work; the event will be routed again."""


def consumer_key(entry: dict[str, Any]) -> str:
    """What identifies one consumer of one event, so a resumed routing does not serve it twice."""
    return f"{entry.get('type')}:{entry.get('target')}:{entry.get('node', '')}"


class EventProcessor:
    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.processed = 0

    def attach(self) -> EventProcessor:
        self.platform.bus.subscribe(self.handle)
        return self

    async def handle(self, event: PlatformEvent, *, strict: bool = False) -> list[dict[str, Any]]:
        """Record *event* and route it to every consumer, at most once each.

        Resumable: an event already routed in full is skipped; one whose routing was interrupted (a crash, or a consumer that could
        not be handed its work) is routed again, skipping consumers already served. With ``strict`` a consumer that could not be
        served raises :class:`EventRoutingFailed`, so the queued job is retried later instead of the failure being swallowed.
        """
        try:
            state = (
                await self.platform.state_for_release(event.env, event.release_id)
                if event.release_id
                else await self.platform.state(event.env)
            )
        except HTTPException:
            logger.warning("event %s for unknown environment %s", event.name, event.env)
            return []
        log = await EventLog.get_or_none(event_id=event.id)
        if log is not None and log.routed_at is not None:
            return list(log.consumers or [])  # at-least-once delivery: already handled
        if log is None:
            try:
                log = await EventLog.create(
                    event_id=event.id,
                    env=event.env,
                    name=event.name,
                    source=event.source,
                    actor=event.actor,
                    payload=event.payload,
                    request_id=event.request_id,
                    occurred_at=event.occurred_at,
                )
            except IntegrityError:
                log = await EventLog.get(event_id=event.id)  # another worker recorded it first
        done = {
            consumer_key(entry): entry
            for entry in (log.consumers or [])
            if entry.get("status") in ("queued", "done")
        }
        consumers = await self.route(state, event, done=done)
        failed = [entry for entry in consumers if entry.get("status") == "failed"]
        log.consumers = consumers
        if not failed:
            log.routed_at = datetime.now(UTC)
        await log.save(update_fields=["consumers", "routed_at"])
        self.processed += 1
        if failed and strict:
            raise EventRoutingFailed(
                f"{len(failed)} consumer(s) of {event.name} could not be served: "
                + "; ".join(f"{e['type']} {e['target']}: {e.get('error')}" for e in failed[:3])
            )
        return consumers

    async def route(
        self,
        state: EnvironmentState,
        event: PlatformEvent,
        *,
        done: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        from pawabase_server.automation.jobs.flows import RunFlowJob
        from pawabase_server.automation.jobs.functions import RunFunctionJob

        platform = self.platform
        consumers: list[dict[str, Any]] = []
        event_input = {"event": event.to_dict()}
        condition_context = {"event": event.to_dict()}

        done = done or {}

        async def attempt(kind: str, target: str, work, node: str = "") -> None:
            entry: dict[str, Any] = {"type": kind, "target": target}
            if node:
                entry["node"] = node
            previous = done.get(consumer_key(entry))
            if previous is not None:
                consumers.append(previous)  # served by an earlier attempt at this event
                return
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
                node=node_id,
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
