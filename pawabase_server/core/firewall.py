"""The firewall's rules: one matcher the gateway enforces and Studio's tester explains.

A **rule** has an ``action`` and conditions. Actions:

* ``allow`` — let the request through and skip every rule after it, managed rules and bans included (an allow-list);
* ``block`` — refuse it (403);
* ``log`` — record it and keep going (watch a pattern before blocking it);
* ``rate_limit`` — count it against ``rate_limit`` (``requests`` per ``window_seconds``, counted ``by`` ip, key, ip_path or
  header:<name>); past the limit, refuse it (429) and, with ``ban_seconds``, ban the address for that long.

A rule in ``simulate`` mode is evaluated and recorded but never acts. A rule may expire (``expires_at``): a temporary block.

**Conditions** are ``{field, op, value}``, combined with ``logic`` ``all`` (default) or ``any``. Fields: ``ip``, ``country``,
``method``, ``path``, ``host``, ``query`` (the raw query string), ``query:<name>``, ``header:<name>``, ``user_agent``, ``origin``,
``referer``, ``role`` (``anon``, ``service``, or ``none`` without a key), ``key_id``, ``content_length``. Operators: ``equals``,
``not_equals``, ``contains``, ``not_contains``, ``starts_with``, ``ends_with``, ``glob``, ``regex``, ``in``, ``not_in`` (lists),
``in_cidr``, ``not_in_cidr`` (lists of ranges), ``exists``, ``missing``, ``gt``, ``lt``. Text comparisons ignore case except
``regex`` and ``glob`` on paths.

The older ``match`` shape (``ips``, ``paths``, ``methods``, ``user_agents``; every key must match, any value of a key) still works and
is combined with the conditions.

**Managed rules** are signatures the platform maintains: SQL injection, cross-site scripting, path traversal, scanners and attack
tools, probes for sensitive files, requests without a user agent, and unusual methods. Each set is off, ``log`` or ``block``.
"""

from __future__ import annotations

import fnmatch
import ipaddress
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib.parse import unquote_plus

LEGACY_KEYS = ("ips", "paths", "methods", "user_agents")
ACTIONS = ("allow", "block", "log", "rate_limit")
FIELDS = (
    "ip",
    "country",
    "method",
    "path",
    "host",
    "query",
    "user_agent",
    "origin",
    "referer",
    "role",
    "key_id",
    "content_length",
)
PREFIXED_FIELDS = ("header:", "query:")
OPS = (
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "glob",
    "regex",
    "in",
    "not_in",
    "in_cidr",
    "not_in_cidr",
    "exists",
    "missing",
    "gt",
    "lt",
)
LIST_OPS = {"in", "not_in", "in_cidr", "not_in_cidr"}
VALUELESS_OPS = {"exists", "missing"}
RATE_BY = ("ip", "key", "ip_path")
MAX_CONDITIONS = 20
MAX_REGEX = 300


class RuleError(ValueError):
    """A rule that can't be saved, and why."""


@dataclass
class Facts:
    """What a rule may look at, worked out once per request."""

    ip: str = ""
    method: str = "GET"
    path: str = "/"
    host: str = ""
    query: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    country: str = ""
    role: str = "none"
    key_id: str = ""
    content_length: int = 0

    @property
    def user_agent(self) -> str:
        return self.headers.get("user-agent", "")

    def value(self, name: str) -> str | None:
        if name.startswith("header:"):
            return self.headers.get(name[7:].lower())
        if name.startswith("query:"):
            return self.params.get(name[6:])
        if name == "user_agent":
            return self.headers.get("user-agent")
        if name in ("origin", "referer"):
            return self.headers.get(name)
        if name == "content_length":
            return str(self.content_length)
        return str(getattr(self, name, "") or "")


# ── validation ──────────────────────────────────────────────────────────


def clean(match: Mapping[str, Any], *, required: bool = True) -> dict[str, list[str]]:
    """The legacy ``match`` with only known keys and trimmed, non-empty values. Raises :class:`RuleError`."""
    unknown = sorted(set(match) - set(LEGACY_KEYS))
    if unknown:
        raise RuleError(f"unknown match key {unknown[0]!r}; use {', '.join(LEGACY_KEYS)}")
    cleaned: dict[str, list[str]] = {}
    for key in LEGACY_KEYS:
        values = [str(v).strip() for v in (match.get(key) or []) if str(v).strip()]
        if not values:
            continue
        if key == "ips":
            for item in values:
                _network(item)
        if key == "methods":
            values = [v.upper() for v in values]
        cleaned[key] = values
    if required and not cleaned:
        raise RuleError(
            "a rule needs at least one thing to match: ips, paths, methods or user_agents"
        )
    return cleaned


def _network(text: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    try:
        return ipaddress.ip_network(text.strip(), strict=False)
    except ValueError as exc:
        raise RuleError(f"{text!r} is not an IP address or CIDR range") from exc


def clean_conditions(conditions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validated conditions. Raises :class:`RuleError` naming the first problem."""
    if len(conditions) > MAX_CONDITIONS:
        raise RuleError(f"a rule has at most {MAX_CONDITIONS} conditions")
    out = []
    for index, raw in enumerate(conditions, start=1):
        name = str(raw.get("field") or "").strip()
        op = str(raw.get("op") or "").strip()
        where = f"condition {index}"
        if name not in FIELDS and not (
            name.startswith(PREFIXED_FIELDS) and len(name.split(":", 1)[1]) > 0
        ):
            raise RuleError(f"{where}: unknown field {name!r}")
        if op not in OPS:
            raise RuleError(f"{where}: unknown operator {op!r}")
        value = raw.get("value")
        if op in VALUELESS_OPS:
            value = None
        elif op in LIST_OPS:
            items = (
                value if isinstance(value, list) else str(value or "").replace("\n", ",").split(",")
            )
            value = [str(v).strip() for v in items if str(v).strip()]
            if not value:
                raise RuleError(f"{where}: give at least one value")
            if op in ("in_cidr", "not_in_cidr"):
                for item in value:
                    _network(item)
        else:
            value = str(value if value is not None else "").strip()
            if not value:
                raise RuleError(f"{where}: give a value")
            if op == "regex":
                if len(value) > MAX_REGEX:
                    raise RuleError(f"{where}: a pattern is at most {MAX_REGEX} characters")
                try:
                    re.compile(value)
                except re.error as exc:
                    raise RuleError(f"{where}: invalid pattern: {exc}") from exc
            if op in ("gt", "lt"):
                try:
                    float(value)
                except ValueError as exc:
                    raise RuleError(f"{where}: {op} needs a number") from exc
        out.append({"field": name, "op": op, "value": value})
    return out


def clean_rate_limit(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    if not raw:
        raise RuleError("a rate-limit rule needs requests and window_seconds")
    try:
        requests = int(raw.get("requests") or 0)
        window = int(raw.get("window_seconds") or 0)
        ban = int(raw.get("ban_seconds") or 0)
    except (TypeError, ValueError) as exc:
        raise RuleError("requests, window_seconds and ban_seconds are whole numbers") from exc
    by = str(raw.get("by") or "ip")
    if not 1 <= requests <= 1_000_000:
        raise RuleError("requests is between 1 and 1,000,000")
    if not 1 <= window <= 86_400:
        raise RuleError("window_seconds is between 1 and 86,400")
    if not 0 <= ban <= 30 * 86_400:
        raise RuleError("ban_seconds is between 0 and 30 days")
    if by not in RATE_BY and not (by.startswith("header:") and len(by) > 7):
        raise RuleError(f"count by {', '.join(RATE_BY)} or header:<name>")
    return {"requests": requests, "window_seconds": window, "by": by, "ban_seconds": ban}


def clean_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    """A rule's ``match``, ``conditions``, ``logic`` and ``rate_limit``, validated together."""
    match = clean(rule.get("match") or {}, required=False)
    conditions = clean_conditions(rule.get("conditions") or [])
    if not match and not conditions:
        raise RuleError("a rule needs at least one condition")
    logic = str(rule.get("logic") or "all")
    if logic not in ("all", "any"):
        raise RuleError("logic is all or any")
    action = str(rule.get("action") or "block")
    if action not in ACTIONS:
        raise RuleError(f"action is one of {', '.join(ACTIONS)}")
    rate = clean_rate_limit(rule.get("rate_limit")) if action == "rate_limit" else None
    return {
        "match": match,
        "conditions": conditions,
        "logic": logic,
        "action": action,
        "rate_limit": rate,
    }


# ── matching ────────────────────────────────────────────────────────────


@lru_cache(maxsize=2048)
def _pattern(text: str) -> re.Pattern[str]:
    return re.compile(text)


@lru_cache(maxsize=4096)
def _networks(items: tuple[str, ...]) -> tuple[Any, ...]:
    out = []
    for item in items:
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue  # a bad stored range matches nothing
    return tuple(out)


def _in_networks(address: str, items: Sequence[str]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in net for net in _networks(tuple(items)))


def condition_holds(condition: Mapping[str, Any], facts: Facts) -> bool:
    name, op, expected = condition["field"], condition["op"], condition.get("value")
    actual = facts.value(name)
    if op == "exists":
        return actual not in (None, "")
    if op == "missing":
        return actual in (None, "")
    if actual is None:
        return op in ("not_equals", "not_contains", "not_in", "not_in_cidr")
    if op in ("in_cidr", "not_in_cidr"):
        hit = _in_networks(actual, expected)
        return hit if op == "in_cidr" else not hit
    if op in ("gt", "lt"):
        try:
            number = float(actual)
        except ValueError:
            return False
        return number > float(expected) if op == "gt" else number < float(expected)
    if op == "regex":
        return _pattern(expected).search(actual) is not None
    if op == "glob":
        return (
            fnmatch.fnmatchcase(actual, expected)
            if name == "path"
            else fnmatch.fnmatch(actual.lower(), expected.lower())
        )
    low = actual.lower()
    if op in ("in", "not_in"):
        hit = low in {str(v).lower() for v in expected}
        return hit if op == "in" else not hit
    want = str(expected).lower()
    return {
        "equals": low == want,
        "not_equals": low != want,
        "contains": want in low,
        "not_contains": want not in low,
        "starts_with": low.startswith(want),
        "ends_with": low.endswith(want),
    }[op]


def matches(match: Mapping[str, Any], *, ip: str, path: str, method: str, user_agent: str) -> bool:
    """The legacy ``match``: every key it has must match, by any of its values."""
    if match.get("ips") and not _in_networks(ip, match["ips"]):
        return False
    if match.get("paths") and not any(fnmatch.fnmatchcase(path, p) for p in match["paths"]):
        return False
    if match.get("methods") and method.upper() not in {m.upper() for m in match["methods"]}:
        return False
    agent = user_agent.lower()
    return not (
        match.get("user_agents") and not any(u.lower() in agent for u in match["user_agents"])
    )


def rule_matches(rule: Mapping[str, Any], facts: Facts) -> bool:
    match = rule.get("match") or {}
    conditions = rule.get("conditions") or []
    if not match and not conditions:
        return False
    if match and not matches(
        match, ip=facts.ip, path=facts.path, method=facts.method, user_agent=facts.user_agent
    ):
        return False
    if not conditions:
        return True
    results = (condition_holds(c, facts) for c in conditions)
    return any(results) if rule.get("logic") == "any" else all(results)


def evaluate(
    rules: Sequence[Mapping[str, Any]], *, ip: str, path: str, method: str, user_agent: str = ""
) -> Mapping[str, Any] | None:
    """The first enabled rule that matches, or ``None`` (legacy entry point: IP, path, method and user agent only)."""
    facts = Facts(
        ip=ip, path=path, method=method, headers={"user-agent": user_agent} if user_agent else {}
    )
    for rule in rules:
        if rule.get("enabled", True) and rule_matches(rule, facts):
            return rule
    return None


# ── managed rules ───────────────────────────────────────────────────────

#: Signature sets: what each catches, and its patterns (searched in the decoded path and query, case-insensitively unless noted).
MANAGED: dict[str, dict[str, Any]] = {
    "sqli": {
        "label": "SQL injection",
        "about": "UNION SELECT, tautologies (' OR 1=1), stacked queries, comment tricks and time-based probes in the URL.",
        "patterns": [
            r"\bunion\b[\s/*]+(all[\s/*]+)?select\b",
            r"'\s*(or|and)\s*'?\d+'?\s*=\s*'?\d+",
            r"'\s*(or|and)\s+'[^']*'\s*=\s*'",
            r";\s*(drop|delete|insert|update|alter|create|truncate)\s",
            r"\b(sleep|pg_sleep|benchmark|waitfor\s+delay)\s*\(",
            r"\binformation_schema\b|\bpg_catalog\b|\bsysobjects\b",
            r"(--|#|/\*)\s*$",
            r"\bxp_cmdshell\b|\binto\s+(out|dump)file\b|\bload_file\s*\(",
        ],
    },
    "xss": {
        "label": "Cross-site scripting",
        "about": "Script tags, javascript: URLs, inline event handlers and data:text/html in the URL.",
        "patterns": [
            r"<\s*script\b",
            r"javascript\s*:",
            r"\bon(error|load|click|mouseover|focus|submit)\s*=",
            r"<\s*(iframe|object|embed|svg|img)\b[^>]*(src|on\w+)\s*=",
            r"data\s*:\s*text/html",
            r"document\.(cookie|domain|write)",
        ],
    },
    "traversal": {
        "label": "Path traversal",
        "about": "../ sequences (plain or encoded) and attempts to read system files.",
        "patterns": [
            r"(\.\.[/\\]){1,}",
            r"%2e%2e(%2f|%5c|/|\\)",
            r"/etc/(passwd|shadow|hosts)\b",
            r"\b(boot|win)\.ini\b",
            r"/proc/self/",
        ],
    },
    "scanners": {
        "label": "Scanners and attack tools",
        "about": "User agents of vulnerability scanners and exploit tools (sqlmap, nikto, nmap, masscan, zgrab, nuclei…).",
        "agents": [
            "sqlmap",
            "nikto",
            "nmap",
            "masscan",
            "zgrab",
            "nuclei",
            "acunetix",
            "wpscan",
            "dirbuster",
            "gobuster",
            "havij",
            "netsparker",
            "w3af",
            "openvas",
            "nessus",
            "jaeles",
            "fuzz faster u fool",
            "ffuf",
            "feroxbuster",
        ],
    },
    "sensitive_files": {
        "label": "Sensitive file probes",
        "about": "Requests for files an API never serves: .env, .git, backups, wp-admin, phpMyAdmin, server-status.",
        "paths": [
            r"/\.env(\.|$|/)",
            r"/\.git(/|$)",
            r"/\.svn(/|$)",
            r"/\.ht(access|passwd)",
            r"\.(bak|old|swp|sql|tar|gz|zip)$",
            r"/wp-(admin|login|content|includes)",
            r"/phpmyadmin",
            r"/server-status",
            r"/\.aws/",
            r"/\.ssh/",
            r"\.php$",
        ],
    },
    "no_user_agent": {
        "label": "No user agent",
        "about": "Requests that send no User-Agent header at all; most real clients send one.",
    },
    "methods": {
        "label": "Unusual methods",
        "about": "TRACE, TRACK, CONNECT, PROPFIND and other methods an API does not use.",
        "methods": [
            "TRACE",
            "TRACK",
            "CONNECT",
            "PROPFIND",
            "PROPPATCH",
            "MKCOL",
            "COPY",
            "MOVE",
            "LOCK",
            "UNLOCK",
        ],
    },
}
MANAGED_ACTIONS = ("off", "log", "block")


@lru_cache(maxsize=64)
def _signature(name: str) -> re.Pattern[str] | None:
    patterns = MANAGED[name].get("patterns") or MANAGED[name].get("paths")
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE) if patterns else None


def clean_managed(raw: Mapping[str, Any]) -> dict[str, str]:
    out = {}
    for name, action in raw.items():
        if name not in MANAGED:
            raise RuleError(f"unknown managed rule set {name!r}")
        if action not in MANAGED_ACTIONS:
            raise RuleError(f"{name}: action is one of {', '.join(MANAGED_ACTIONS)}")
        out[name] = action
    return out


def managed_hit(settings: Mapping[str, str], facts: Facts) -> tuple[str, str] | None:
    """The first managed set that is on and matches, as ``(set, action)``."""
    target = None
    for name, action in settings.items():
        if action == "off" or name not in MANAGED:
            continue
        spec = MANAGED[name]
        if name in ("sqli", "xss", "traversal"):
            if target is None:
                raw = f"{facts.path}?{facts.query}" if facts.query else facts.path
                target = f"{raw} {unquote_plus(unquote_plus(raw))}"
            if _signature(name).search(target):
                return name, action
        elif name == "sensitive_files":
            if _signature(name).search(facts.path):
                return name, action
        elif name == "scanners":
            agent = facts.user_agent.lower()
            if agent and any(tool in agent for tool in spec["agents"]):
                return name, action
        elif name == "no_user_agent":
            if not facts.user_agent.strip():
                return name, action
        elif name == "methods":
            if facts.method.upper() in spec["methods"]:
                return name, action
    return None


# ── auto-ban ────────────────────────────────────────────────────────────


def clean_auto_ban(raw: Mapping[str, Any]) -> dict[str, Any]:
    try:
        threshold = int(raw.get("threshold") or 20)
        window = int(raw.get("window_seconds") or 60)
        ban = int(raw.get("ban_seconds") or 3600)
    except (TypeError, ValueError) as exc:
        raise RuleError("threshold, window_seconds and ban_seconds are whole numbers") from exc
    statuses = [int(s) for s in (raw.get("statuses") or [401, 403, 404, 429])]
    if not 2 <= threshold <= 100_000:
        raise RuleError("threshold is between 2 and 100,000")
    if not 5 <= window <= 86_400:
        raise RuleError("window_seconds is between 5 and 86,400")
    if not 60 <= ban <= 30 * 86_400:
        raise RuleError("ban_seconds is between a minute and 30 days")
    if not statuses or any(not 400 <= s <= 599 for s in statuses):
        raise RuleError("statuses are HTTP error statuses (400–599)")
    exempt = [str(e).strip() for e in (raw.get("exempt") or []) if str(e).strip()]
    for item in exempt:
        _network(item)
    return {
        "enabled": bool(raw.get("enabled")),
        "threshold": threshold,
        "window_seconds": window,
        "ban_seconds": ban,
        "statuses": sorted(set(statuses)),
        "exempt": exempt,
    }
