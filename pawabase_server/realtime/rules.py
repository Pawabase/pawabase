"""Channel rules: who may subscribe, publish and be present on which channels, as an environment configures them."""

from __future__ import annotations

import fnmatch
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pawabase_server.core.templating import render
from pawabase_server.policies import Policy, PolicyEngine
from pawabase_server.policies.engine import validate_condition

DEFAULT_POLICY = "authenticated"
MAX_RULE_HISTORY = 1000
_TEMPLATE = re.compile(r"\{\{.*?\}\}")


@dataclass
class ChannelRule:
    """Who may do what on channels matching a pattern.

    Patterns may use ``*`` and templates: ``user:{{ auth.user_id }}`` gives every signed-in user a private channel only they can join.
    """

    pattern: str
    subscribe: Any = DEFAULT_POLICY
    publish: Any = DEFAULT_POLICY
    presence: bool = True
    history: int = 50

    @property
    def templated(self) -> bool:
        return "{{" in self.pattern

    def claims(self, channel: str) -> bool:
        """Whether this rule governs *channel*: templates count as wildcards.

        ``user:{{ auth.user_id }}`` governs every ``user:*`` channel, so a private channel of someone else is refused by this rule rather
        than falling through to the default policy.
        """
        return fnmatch.fnmatchcase(channel, _TEMPLATE.sub("*", self.pattern))

    def permits(self, channel: str, auth: Mapping[str, Any]) -> bool:
        """For templated rules, whether the rendered pattern names *channel*; other rules permit any channel they claim."""
        if not self.templated:
            return True
        rendered = str(render(self.pattern, {"auth": auth}))
        return "None" not in rendered and fnmatch.fnmatchcase(channel, rendered)


@dataclass
class ChannelConfig:
    """An environment's realtime rules at one version."""

    version: int
    rules: list[ChannelRule]
    engine: PolicyEngine
    allow_client_publish: bool = True
    default_policy: Any = DEFAULT_POLICY
    loaded_at: float = field(default_factory=time.monotonic)

    def rule_for(self, channel: str) -> ChannelRule:
        """The first rule that claims *channel*, or a rule that applies the environment's default policy."""
        for rule in self.rules:
            if rule.claims(channel):
                return rule
        return ChannelRule(
            pattern=channel, subscribe=self.default_policy, publish=self.default_policy
        )


def _history(value: Any) -> int:
    try:
        return max(0, min(int(value), MAX_RULE_HISTORY))
    except (TypeError, ValueError):
        return 50


def build_config(payload: Mapping[str, Any]) -> ChannelConfig:
    """A :class:`ChannelConfig` from what the API sent.

    A malformed policy or rule is skipped rather than failing the whole environment: one bad row in Studio must not take realtime down.
    Stored nulls count as unset, because Studio's "Default" option and a hand-edited settings document both store them.
    """
    policies = {}
    for name, definition in (payload.get("policies") or {}).items():
        try:
            validate_condition(definition.get("condition"))
            policies[name] = Policy(
                name, definition.get("condition"), definition.get("description", "")
            )
        except Exception:
            continue
    rules = []
    for rule in payload.get("channels") or []:
        pattern = rule.get("pattern") if isinstance(rule, Mapping) else None
        if not pattern or not isinstance(pattern, str):
            continue
        rules.append(
            ChannelRule(
                pattern=pattern,
                subscribe=rule.get("subscribe") or DEFAULT_POLICY,
                publish=rule.get("publish") or DEFAULT_POLICY,
                presence=bool(rule.get("presence", True)),
                history=_history(rule.get("history", 50)),
            )
        )
    return ChannelConfig(
        version=int(payload.get("version") or 0),
        rules=rules,
        engine=PolicyEngine(policies),
        allow_client_publish=bool(payload.get("allow_client_publish", True)),
        default_policy=payload.get("default_policy") or DEFAULT_POLICY,
    )
