"""What an API key's scopes let it do.

A key with no scopes may do anything its role allows. A key with scopes may make only the requests its scopes name, whichever route
they reach: the edge checks every request against the catalog below, so a route cannot forget to ask. A scope is ``family:read`` (looking: ``GET``/``HEAD``/``OPTIONS``) or ``family:write`` (changing: every other method) over the paths the family owns; ``family:*`` is both, and ``*``
is everything.

Application scopes (``data``, ``storage``, ``auth``, ``flows``, ``realtime``) work on the paths an app's clients use. Management scopes
(``users``, ``webhooks``, ``keys``…) work on the environment's management API, which only a secret key reaches; ``admin`` is all of it,
including the project-wide routes (creating environments, promoting, releases) that no narrower scope covers.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ENV = "/platform/v1/envs/*/"


@dataclass(frozen=True, slots=True)
class Family:
    id: str
    group: str
    label: str
    description: str
    patterns: tuple[str, ...]
    #: Whether only a secret key may hold the family's scopes (the management API is out of a browser's reach).
    management: bool = False
    #: ``read``, ``write`` or both, and what each is called to the person choosing.
    read: str = ""
    write: str = ""


def _env(*segments: str) -> tuple[str, ...]:
    out: list[str] = []
    for segment in segments:
        out += [f"{ENV}{segment}", f"{ENV}{segment}/*"]
    return tuple(out)


FAMILIES: tuple[Family, ...] = (
    Family("data", "Your app", "Data", "Rows of your resources and your custom routes.", ("/rest/*",),
           read="Read rows", write="Create, change and delete rows"),
    Family("storage", "Your app", "Files", "Objects in your buckets.", ("/storage/v1/*",),
           read="Download and list files", write="Upload, replace and delete files"),
    Family("auth", "Your app", "Sign-in", "Your app's own users signing up and in.", ("/auth/v1/*",),
           read="Read the signed-in user", write="Sign up, sign in, change a profile"),
    Family("flows", "Your app", "Flows", "Running your flows over HTTP.", ("/flows/v1/*",),
           read="Read a run's result", write="Start a flow"),
    Family("realtime", "Your app", "Realtime", "Live channels.", ("/realtime/v1/*",),
           read="Listen to channels", write="Publish messages"),
    Family("users", "People", "Users", "The people who sign in to your app.",
           _env("auth/users", "auth/orgs", "auth/sessions", "auth/identities", "auth/events", "auth/api-keys", "auth/devices", "auth/invitations", "auth/stats"), True,
           "List and read users", "Create, edit, disable and delete users"),
    Family("roles", "People", "Roles and permissions", "What each role may do. Giving a role to a user needs the Users area.",
           _env("auth/roles"), True, "List roles and permissions", "Create, change and delete roles"),
    Family("resources", "Data", "Tables, policies and routes", "The shape of your data and API: tables, schemas, transformers, policies and custom routes.",
           _env("resources", "schema", "schemas", "types", "blueprint", "transformers", "policies", "routes"), True,
           "Read tables, policies and routes", "Create and change them, and migrate tables"),
    Family("database", "Data", "Database", "The SQL console and its tools.",
           _env("database", "cache"), True, "Browse the database", "Run changes against it"),
    Family("buckets", "Data", "Buckets", "Storage buckets and their rules.",
           _env("buckets"), True, "List buckets", "Create, change and delete buckets"),
    Family("backups", "Data", "Backups", "Snapshots and restores.",
           _env("snapshots", "backup", "backup-schedule", "restore"), True,
           "List backups", "Take backups and restore"),
    Family("webhooks", "Automation", "Webhooks", "Outbound and inbound webhooks and their deliveries.",
           _env("webhooks", "inbound-hooks", "webhook-deliveries", "subscriptions"), True,
           "List webhooks and deliveries", "Create, change and delete webhooks"),
    Family("events", "Automation", "Events and jobs", "Events, schedules and background jobs.",
           _env("events", "events-graph", "schedules", "jobs", "failed-jobs", "queues"), True,
           "Read events, schedules and jobs", "Send events, schedule and retry jobs"),
    Family("flowdefs", "Automation", "Flow definitions", "The flows themselves and their runs.",
           (*_env("flows", "flow-runs"), "/platform/v1/blocks", "/platform/v1/flows/validate"), True,
           "Read flows, runs and the block catalogue", "Create, edit and run flows"),
    Family("mail", "Automation", "Mail", "Sending email and reading the mail log.",
           _env("mail"), True, "Read the mail log", "Send mail"),
    Family("templates", "Automation", "Mail templates", "The email templates flows send.",
           _env("mail-templates"), True, "Read templates", "Create, edit and delete templates"),
    Family("channels", "Automation", "Realtime channels", "Channel rules and connections.",
           _env("realtime-channels"), True, "Read channel rules", "Change channel rules"),
    Family("keys", "Security", "API keys", "Making and revoking keys.",
           _env("keys"), True, "List keys", "Create, rotate and revoke keys"),
    Family("secrets", "Security", "Secrets and settings", "Environment secrets and configuration.",
           _env("secrets", "configuration"), True, "Read settings", "Change secrets and settings"),
    Family("firewall", "Security", "Firewall and domains", "Blocking rules and custom domains.",
           _env("firewall", "firewall-config", "firewall-bans", "firewall-events", "firewall-order",
                "firewall-stats", "firewall-test", "domains"), True,
           "Read rules and domains", "Change rules and domains"),
    Family("monitoring", "Monitoring", "Monitoring", "Logs, requests, metrics, alarms and usage.",
           _env("observability", "logs", "requests", "metrics", "metric-series", "metric-catalog", "alarms",
                "alarm-events", "incidents", "service-health", "analytics", "advisor", "overview",
                "status-page", "plan-usage", "dashboards", "openapi", "status"), True,
           "Read logs, metrics and usage", "Manage alarms and dashboards"),
    Family("admin", "Administration", "Everything", "The whole management API, including project-wide routes: environments, releases, promotion and the audit log.",
           ("/platform/v1/*",), True, "Read anything", "Change anything"),
)
BY_ID = {family.id: family for family in FAMILIES}

#: Names older keys carry, kept working with the rules they always meant.
LEGACY = {
    "resource:read": ("data", "read"),
    "resource:write": ("data", "write"),
    "routes:invoke": ("data", "*"),
}

#: Ready-made choices: a name, what it is for, and the scopes it grants.
PRESETS: tuple[dict[str, Any], ...] = (
    {"id": "full", "label": "Full access", "description": "Everything the key's role allows. The default.", "scopes": []},
    {"id": "app-read", "label": "Read-only app", "description": "Read rows and files; change nothing.", "scopes": ["data:read", "storage:read"]},
    {"id": "app", "label": "App backend", "description": "Everything your app's own code does: data, files, sign-in, flows, realtime.",
     "scopes": ["data:*", "storage:*", "auth:*", "flows:*", "realtime:*"]},
    {"id": "resources-only", "label": "Resources only", "description": "Rows and the shape of your data, nothing else.",
     "scopes": ["data:*", "resources:*"]},
    {"id": "users-read", "label": "User directory (read)", "description": "List and look up users.", "scopes": ["users:read"]},
    {"id": "users", "label": "User manager", "description": "Create, edit and disable users.", "scopes": ["users:*"]},
    {"id": "webhooks", "label": "Webhook manager", "description": "Manage webhooks and see their deliveries.", "scopes": ["webhooks:*"]},
    {"id": "monitoring", "label": "Monitoring (read)", "description": "Logs, metrics and usage, for a dashboard or an alerting tool.", "scopes": ["monitoring:read"]},
    {"id": "deploy", "label": "Deploy and configure", "description": "Change tables, policies, routes, flows, templates, roles, buckets, webhooks and schedules: what a deploy pipeline does.",
     "scopes": ["resources:*", "roles:*", "flowdefs:*", "templates:*", "secrets:*", "buckets:*", "webhooks:*", "events:*"]},
    {"id": "admin", "label": "Administrator", "description": "Manage the whole infrastructure.", "scopes": ["admin:*"]},
)


def scope_ids() -> list[str]:
    out = ["*"]
    for family in FAMILIES:
        out += [f"{family.id}:read", f"{family.id}:write", f"{family.id}:*"]
    return out


def validate(scopes: list[str], *, role: str) -> list[str]:
    """The scopes cleaned of duplicates. Raises ``ValueError`` for a name nobody defined, or a management scope on a publishable key."""
    clean: list[str] = []
    known = set(scope_ids()) | set(LEGACY)
    for scope in scopes:
        scope = scope.strip()
        if scope not in known:
            raise ValueError(f"unknown scope {scope!r}; the key-scopes list names every scope")
        family = scope.split(":", 1)[0]
        if role != "secret" and family in BY_ID and BY_ID[family].management:
            raise ValueError(f"{scope!r} manages the environment, so only a secret key can hold it")
        if scope not in clean:
            clean.append(scope)
    return clean


def _grants(scope: str) -> list[tuple[Family, str]]:
    """The ``(family, kind)`` pairs a scope grants; ``kind`` is ``read``, ``write`` or ``*``."""
    if scope == "*":
        return [(family, "*") for family in FAMILIES]
    if scope in LEGACY:
        family_id, kind = LEGACY[scope]
        return [(BY_ID[family_id], kind)]
    family_id, _, kind = scope.partition(":")
    family = BY_ID.get(family_id)
    return [(family, kind)] if family is not None and kind in ("read", "write", "*") else []


def allows(scopes: list[str] | tuple[str, ...], method: str, path: str) -> bool:
    """Whether a key holding *scopes* may make this request. No scopes means no restriction."""
    if not scopes:
        return True
    if path == "/health/v1" or path.startswith("/health/v1/"):
        return True  # proving the key resolves needs no scope
    reading = method.upper() in READ_METHODS
    for scope in scopes:
        for family, kind in _grants(scope):
            if kind == "read" and not reading or kind == "write" and reading:
                continue
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in family.patterns):
                return True
    return False


def covers(held: list[str] | tuple[str, ...], wanted: list[str]) -> bool:
    """Whether a key holding *held* may hand out *wanted*: a key can never make one stronger than itself."""
    if not held:
        return True
    if "*" in held or "admin:*" in held:
        return True
    for scope in wanted:
        family = scope.split(":", 1)[0]
        if scope == "*" or scope not in held and f"{family}:*" not in held:
            return False
    return bool(wanted)


def catalog() -> dict[str, Any]:
    """The choices, shaped for a picker."""
    return {
        "families": [
            {
                "id": f.id,
                "group": f.group,
                "label": f.label,
                "description": f.description,
                "management": f.management,
                "read": {"scope": f"{f.id}:read", "label": f.read},
                "write": {"scope": f"{f.id}:write", "label": f.write},
            }
            for f in FAMILIES
        ],
        "presets": list(PRESETS),
    }
