"""Alarms: watch a metric, and tell people by mail when it crosses a line for long enough.

The scheduler calls :func:`evaluate_all` every minute. A rule fires after ``breaches_needed`` checks in a row
cross the line, and recovers on the first check that does not. Each change of state is recorded, published as
an ``alarm.state_changed`` event (so flows can react) and mailed through the environment's own provider.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app import insights
from database.models import AlarmEvent, AlarmRule

if TYPE_CHECKING:
    from app.platform import Platform

logger = logging.getLogger("pawabase.alarms")

COMPARISONS = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}
WORDS = {"gt": "above", "gte": "at or above", "lt": "below", "lte": "at or below"}


def breached(value: float, comparison: str, threshold: float) -> bool:
    return {
        "gt": value > threshold,
        "gte": value >= threshold,
        "lt": value < threshold,
        "lte": value <= threshold,
    }[comparison]


def describe(rule: AlarmRule, value: float | None) -> str:
    spec = insights.METRICS[rule.metric]
    unit = "" if spec["unit"] == "count" else spec["unit"]
    shown = "no data" if value is None else f"{value:g}{unit}"
    window = "" if spec["instant"] else f" over the last {rule.window_minutes} minutes"
    return f"{spec['label']} is {shown}{window}; the line is {WORDS[rule.comparison]} {rule.threshold:g}{unit}."


async def notify(
    platform: Platform, rule: AlarmRule, to_state: str, value: float | None
) -> tuple[bool, str | None]:
    """Mail the rule's recipients. Returns (sent, why not)."""
    recipients = [str(item) for item in (rule.recipients or []) if item]
    if not recipients:
        return False, "no recipients"
    state = await platform.state(rule.env)
    headline = {
        "alarm": f"[ALARM] {rule.name}",
        "ok": f"[OK] {rule.name} has recovered",
    }.get(to_state, f"[{to_state.upper()}] {rule.name}")
    body = (
        f"{headline}\n\n"
        f"Environment: {rule.env}\n"
        f"{describe(rule, value)}\n"
        + (f"\n{rule.description}\n" if rule.description else "")
        + "\nOpen Studio, Observability, Alarms to see its history or mute it."
    )
    try:
        await platform.mail.send(state, recipients, headline, text=body)
    except Exception as exc:  # a mail failure must not stop the alarm from being recorded
        logger.warning("alarm %s could not be mailed: %s", rule.name, exc)
        return False, str(exc)[:300]
    return True, None


async def evaluate(
    platform: Platform, rule: AlarmRule, *, now: datetime | None = None
) -> AlarmEvent | None:
    """Check one rule. Returns the event if its state changed."""
    now = now or datetime.now(UTC)
    value = await insights.value(rule.env, rule.metric, rule.window_minutes)
    rule.last_checked_at = now
    rule.last_value = value
    previous = rule.state
    target = previous
    if value is not None:
        if breached(value, rule.comparison, rule.threshold):
            rule.breach_count += 1
            if rule.breach_count >= max(1, rule.breaches_needed):
                target = "alarm"
            elif previous == "unknown":
                target = "ok"
        else:
            rule.breach_count = 0
            target = "ok"
    event = None
    muted = bool(rule.muted_until and rule.muted_until > now)
    if target != previous:
        rule.state, rule.state_since = target, now
        sent, why = (False, "muted") if muted else (False, None)
        # Going from unknown to ok is just the first check, not news.
        if not muted and not (previous == "unknown" and target == "ok"):
            sent, why = await notify(platform, rule, target, value)
            if sent:
                rule.last_notified_at = now
        event = await AlarmEvent.create(
            rule=rule,
            env=rule.env,
            from_state=previous,
            to_state=target,
            value=value,
            message=describe(rule, value),
            notified=sent,
            error=why if not sent and why else None,
        )
        try:
            await platform.emit(
                await platform.state(rule.env),
                "alarm.state_changed",
                {
                    "alarm": rule.name,
                    "metric": rule.metric,
                    "from": previous,
                    "to": target,
                    "value": value,
                },
                actor="alarms",
            )
        except Exception:  # events are best effort here
            logger.debug("could not publish the alarm event", exc_info=True)
    elif (
        target == "alarm"
        and rule.renotify_minutes
        and not muted
        and (
            rule.last_notified_at is None
            or now - rule.last_notified_at >= timedelta(minutes=rule.renotify_minutes)
        )
    ):
        sent, _ = await notify(platform, rule, "alarm", value)
        if sent:
            rule.last_notified_at = now
    await rule.save()
    return event


async def evaluate_all(platform: Platform) -> int:
    """Check every enabled rule of every environment. Returns how many were checked."""
    checked = 0
    for rule in await AlarmRule.filter(enabled=True):
        try:
            await evaluate(platform, rule)
            checked += 1
        except Exception:
            logger.exception("alarm %s/%s could not be checked", rule.env, rule.name)
    return checked


def public(rule: AlarmRule) -> dict[str, Any]:
    from routes.common import dump

    data = dump(rule)
    data["metric_label"] = insights.METRICS.get(rule.metric, {}).get("label", rule.metric)
    data["unit"] = insights.METRICS.get(rule.metric, {}).get("unit", "")
    data["muted"] = bool(rule.muted_until and rule.muted_until > datetime.now(UTC))
    return data
