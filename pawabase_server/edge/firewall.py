"""The firewall at the edge: every request to an environment's public API, with or without a key, before it reaches anything.

Order, for one request:

1. an active **ban** on the address refuses it, unless an enforced ``allow`` rule matches the request (an allow-list beats bans);
2. **rules** in order: the first ``allow`` or ``block`` that matches decides (``allow`` also skips the managed rules); ``log``
   records and goes on; ``rate_limit`` counts and refuses past the limit (429), optionally banning the address; a ``simulate`` rule
   only records;
3. **managed rules** (attack signatures) record or refuse.

After the response, an error status counts toward **automatic bans** when the environment turned them on.

The configuration is read from the database per environment and kept for ``CONFIG_SECONDS``; a change in Studio calls
:meth:`Firewall.invalidate`. Events and rule hit counts are written in batches every ``FLUSH_SECONDS``, never on the request path.
Rate counters live in Redis when the installation has one (shared by every process), otherwise in this process.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from tortoise.expressions import F, Q

from pawabase_server.core import firewall as rules_engine
from pawabase_server.core.namespace import scoped
from pawabase_server.db.models import FirewallBan, FirewallConfig, FirewallEvent, FirewallRule
from pawabase_server.edge.errors import WS_FORBIDDEN, Refusal
from pawabase_server.edge.request import Request

logger = logging.getLogger("pawabase.firewall")

CONFIG_SECONDS = 5.0
FLUSH_SECONDS = 2.0
MAX_BUFFER = 5000
#: Keyless routes that name their environment in the path, and where.
PATH_ENV = {
    "/hooks/v1/": 3,
    "/docs/v1/": 3,
    "/storage/v1/files/": 4,
    "/storage/v1/signed/": 4,
}


@dataclass
class Config:
    rules: list[dict[str, Any]] = field(default_factory=list)
    managed: dict[str, str] = field(default_factory=dict)
    auto_ban: dict[str, Any] = field(default_factory=dict)
    bans: list[tuple[Any, str]] = field(default_factory=list)  # (network, ban id)

    @property
    def empty(self) -> bool:
        return not (
            self.rules
            or any(a != "off" for a in self.managed.values())
            or self.bans
            or self.auto_ban.get("enabled")
        )


def _network(text: str) -> Any:
    try:
        return ipaddress.ip_network(text, strict=False)
    except ValueError:
        return None


def _address(text: str) -> Any:
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


class Firewall:
    def __init__(
        self,
        *,
        redis_url: str = "",
        country_header: str = "",
        run: Callable[[Callable[[], Awaitable[Any]]], Awaitable[Any]] | None = None,
    ) -> None:
        #: Runs database work in a database context: the edge sits outside the application's own middleware.
        self._run = run or (lambda work: work())
        self.country_header = country_header.lower().strip()
        self._configs: dict[str, tuple[float, Config]] = {}
        self._events: list[dict[str, Any]] = []
        self._hits: dict[str, int] = {}
        self._ban_hits: dict[str, int] = {}
        self._local: dict[str, tuple[int, int]] = {}
        self._errors: dict[tuple[str, str], deque[float]] = {}
        self._redis = None
        self._redis_url = redis_url
        self._task: asyncio.Task | None = None
        self.dropped = 0
        self.stats = {"checked": 0, "blocked": 0, "limited": 0, "logged": 0, "banned": 0}

    # ── configuration ────────────────────────────────────────────────────

    async def config(self, env: str) -> Config:
        cached = self._configs.get(env)
        now = time.monotonic()
        if cached and cached[0] > now:
            return cached[1]
        config = await self._run(lambda: self._load(env))
        self._configs[env] = (now + CONFIG_SECONDS, config)
        return config

    async def _load(self, env: str) -> Config:
        moment = datetime.now(UTC)
        rows = await FirewallRule.filter(env=env, enabled=True).filter(
            Q(expires_at=None) | Q(expires_at__gt=moment)
        )
        settings = await FirewallConfig.get_or_none(env=env)
        bans = await FirewallBan.filter(env=env).filter(
            Q(expires_at=None) | Q(expires_at__gt=moment)
        )
        config = Config(
            rules=[
                {
                    "id": r.id,
                    "name": r.name,
                    "action": r.action,
                    "mode": r.mode,
                    "match": r.match or {},
                    "conditions": r.conditions or [],
                    "logic": r.logic or "all",
                    "rate_limit": r.rate_limit,
                }
                for r in rows
            ],
            managed=dict((settings.managed if settings else {}) or {}),
            auto_ban=dict((settings.auto_ban if settings else {}) or {}),
            bans=[(net, b.id) for b in bans if (net := _network(b.ip)) is not None],
        )
        return config

    def invalidate(self, env: str) -> None:
        self._configs.pop(env, None)

    # ── the request ──────────────────────────────────────────────────────

    @staticmethod
    def environment_of(request: Request, context: Any, served: str | None) -> str | None:
        """The environment the request will be served by: its key's, the hostname's, or the one its path names (keyless routes)."""
        if context is not None:
            return context.env
        if served:
            return served
        for prefix, index in PATH_ENV.items():
            if request.path.startswith(prefix):
                parts = request.path.split("/")
                return parts[index] if len(parts) > index and parts[index] else None
        return None

    def facts(self, request: Request, context: Any) -> rules_engine.Facts:
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        return rules_engine.Facts(
            ip=request.client_ip,
            method=request.method,
            path=request.path,
            host=request.host,
            query=request.scope.get("query_string", b"").decode("latin-1"),
            headers=request.headers,
            params=dict(request.query),
            country=(
                request.headers.get(self.country_header, "")
                if self.country_header and request.via_proxy
                else ""
            ).upper(),
            role=getattr(context, "role", None) or "none",
            key_id=str(getattr(context, "key_id", "") or ""),
            content_length=length,
        )

    async def check(self, request: Request, context: Any, env: str | None) -> Refusal | None:
        """The firewall's decision for one request: a refusal, or ``None`` to let it through."""
        if not env:
            return None
        config = await self.config(env)
        if config.empty:
            return None
        self.stats["checked"] += 1
        facts = self.facts(request, context)
        enforced = [r for r in config.rules if r.get("mode") != "simulate"]
        allowed = any(
            r["action"] == "allow" and rules_engine.rule_matches(r, facts) for r in enforced
        )
        # 1. bans, unless an enforced allow rule matches the request (an allow-list beats bans)
        address = _address(facts.ip)
        if address is not None and not allowed:
            for network, ban_id in config.bans:
                if address.version == network.version and address in network:
                    self._ban_hits[ban_id] = self._ban_hits.get(ban_id, 0) + 1
                    self._record(env, facts, "ban", "block", None)
                    self.stats["blocked"] += 1
                    return Refusal(
                        403,
                        "ip_banned",
                        "This address is temporarily blocked.",
                        ws_code=WS_FORBIDDEN,
                    )
        # 2. rules, in order: the first allow or block decides; log and rate_limit go on.
        for rule in config.rules:
            if not rules_engine.rule_matches(rule, facts):
                continue
            simulated = rule.get("mode") == "simulate"
            self._hits[rule["id"]] = self._hits.get(rule["id"], 0) + 1
            if rule["action"] == "allow":
                if simulated:
                    self._record(env, facts, "rule", "allow", rule, simulated=True)
                    continue
                return None  # past everything below, managed rules included
            if rule["action"] == "log":
                self._record(env, facts, "rule", "log", rule)
                self.stats["logged"] += 1
                continue
            if rule["action"] == "block":
                self._record(env, facts, "rule", "block", rule, simulated=simulated)
                if simulated:
                    continue
                self.stats["blocked"] += 1
                return Refusal(
                    403,
                    "firewall_blocked",
                    f"This request was blocked by a firewall rule ({rule['name']}).",
                    ws_code=WS_FORBIDDEN,
                )
            if rule["action"] == "rate_limit":
                limit = rule.get("rate_limit") or {}
                if not await self._within_rate(env, rule["id"], limit, facts):
                    self._record(env, facts, "rate_limit", "rate_limit", rule, simulated=simulated)
                    if simulated:
                        continue
                    self.stats["limited"] += 1
                    if limit.get("ban_seconds"):
                        await self.ban(
                            env,
                            facts.ip,
                            source="rate_limit",
                            seconds=int(limit["ban_seconds"]),
                            reason=f"Over the rate limit of rule {rule['name']}",
                        )
                    window = int(limit.get("window_seconds") or 60)
                    return Refusal(
                        429,
                        "firewall_rate_limited",
                        f"Too many requests; the limit is {limit.get('requests')} per {window} seconds.",
                        ws_code=WS_FORBIDDEN,
                        headers=((b"retry-after", str(window).encode()),),
                    )
        # 3. managed rules
        hit = rules_engine.managed_hit(config.managed, facts)
        if hit is not None:
            name, action = hit
            label = rules_engine.MANAGED[name]["label"]
            self._record(env, facts, "managed", action, {"id": None, "name": label})
            if action == "block":
                self.stats["blocked"] += 1
                return Refusal(
                    403,
                    "firewall_blocked",
                    f"This request was blocked by the firewall ({label}).",
                    ws_code=WS_FORBIDDEN,
                )
            self.stats["logged"] += 1
        return None

    # ── rate limits ──────────────────────────────────────────────────────

    async def _within_rate(
        self, env: str, rule_id: str, limit: dict[str, Any], facts: rules_engine.Facts
    ) -> bool:
        window = max(1, int(limit.get("window_seconds") or 60))
        requests = max(1, int(limit.get("requests") or 1))
        by = str(limit.get("by") or "ip")
        if by == "key":
            who = facts.key_id or facts.ip
        elif by == "ip_path":
            who = f"{facts.ip}|{facts.path}"
        elif by.startswith("header:"):
            who = facts.headers.get(by[7:].lower()) or facts.ip
        else:
            who = facts.ip
        slot = int(time.time() // window)
        key = f"{env}:{rule_id}:{who}:{slot}"
        count = await self._increment(key, window)
        return count <= requests

    async def _increment(self, key: str, window: int) -> int:
        redis = self._redis_client()
        if redis is not None:
            try:
                name = scoped(f"pawabase:firewall:{key}")
                count = await redis.incr(name)
                if count == 1:
                    await redis.expire(name, window + 1)
                return int(count)
            except Exception:
                logger.warning(
                    "firewall rate counter: Redis unavailable, counting in this process",
                    exc_info=True,
                )
        now = int(time.time())
        expires, count = self._local.get(key, (now + window + 1, 0))
        count += 1
        self._local[key] = (expires, count)
        if len(self._local) > 50_000:
            self._local = {k: v for k, v in self._local.items() if v[0] > now}
        return count

    def _redis_client(self) -> Any:
        if not self._redis_url:
            return None
        if self._redis is None:
            import redis.asyncio as aioredis  # optional dependency, present when Redis is configured

            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    # ── bans ─────────────────────────────────────────────────────────────

    async def ban(
        self, env: str, ip: str, *, source: str, seconds: int | None, reason: str
    ) -> FirewallBan:
        return await self._run(
            lambda: self._ban(env, ip, source=source, seconds=seconds, reason=reason)
        )

    async def _ban(
        self, env: str, ip: str, *, source: str, seconds: int | None, reason: str
    ) -> FirewallBan:
        expires = datetime.now(UTC) + timedelta(seconds=seconds) if seconds else None
        existing = (
            await FirewallBan.filter(env=env, ip=ip)
            .filter(Q(expires_at=None) | Q(expires_at__gt=datetime.now(UTC)))
            .first()
        )
        if existing is not None:
            return existing
        row = await FirewallBan.create(
            env=env, ip=ip, source=source, reason=reason[:300], expires_at=expires
        )
        self.stats["banned"] += 1
        self.invalidate(env)
        logger.info("firewall: banned %s in %s (%s)", ip, env, reason)
        return row

    async def observe(self, env: str | None, ip: str, status: int) -> None:
        """Count an error response toward an automatic ban."""
        if not env or status < 400:
            return
        config = await self.config(env)
        rule = config.auto_ban
        if not rule.get("enabled") or status not in (rule.get("statuses") or []):
            return
        address = _address(ip)
        if address is None or any(
            (net := _network(item)) is not None
            and address.version == net.version
            and address in net
            for item in rule.get("exempt") or []
        ):
            return
        if any(address.version == net.version and address in net for net, _ in config.bans):
            return
        now = time.monotonic()
        window = float(rule.get("window_seconds") or 60)
        seen = self._errors.setdefault((env, ip), deque())
        seen.append(now)
        while seen and seen[0] < now - window:
            seen.popleft()
        if len(seen) >= int(rule.get("threshold") or 20):
            seen.clear()
            await self.ban(
                env,
                ip,
                source="auto",
                seconds=int(rule.get("ban_seconds") or 3600),
                reason=f"{rule.get('threshold')} error responses within {int(window)} seconds",
            )
        if len(self._errors) > 50_000:
            self._errors = {k: v for k, v in self._errors.items() if v and v[-1] > now - window}

    # ── recording ────────────────────────────────────────────────────────

    def _record(
        self,
        env: str,
        facts: rules_engine.Facts,
        source: str,
        action: str,
        rule: dict[str, Any] | None,
        *,
        simulated: bool = False,
    ) -> None:
        if len(self._events) >= MAX_BUFFER:
            self.dropped += 1
            return
        self._events.append(
            {
                "env": env,
                "ip": facts.ip[:64],
                "method": facts.method[:10],
                "path": facts.path[:1024],
                "country": facts.country[:8],
                "user_agent": facts.user_agent[:300],
                "source": source,
                "action": action,
                "rule_id": rule.get("id") if rule else None,
                "rule_name": (rule.get("name") if rule else "")[:120],
                "simulated": simulated,
            }
        )

    async def flush(self) -> int:
        return await self._run(self._flush)

    async def _flush(self) -> int:
        events, self._events = self._events, []
        written = len(events)
        hits, self._hits = self._hits, {}
        ban_hits, self._ban_hits = self._ban_hits, {}
        try:
            if events:
                await FirewallEvent.bulk_create([FirewallEvent(**e) for e in events])
                events = []
            now = datetime.now(UTC)
            while hits:
                rule_id, count = next(iter(hits.items()))
                await FirewallRule.filter(id=rule_id).update(
                    hits=F("hits") + count, last_hit_at=now
                )
                hits.pop(rule_id)
            while ban_hits:
                ban_id, count = next(iter(ban_hits.items()))
                await FirewallBan.filter(id=ban_id).update(hits=F("hits") + count)
                ban_hits.pop(ban_id)
        except Exception:
            # Put back what was not written, so the next batch retries it.
            self._events = (events + self._events)[:MAX_BUFFER]
            for rule_id, count in hits.items():
                self._hits[rule_id] = self._hits.get(rule_id, 0) + count
            for ban_id, count in ban_hits.items():
                self._ban_hits[ban_id] = self._ban_hits.get(ban_id, 0) + count
            raise
        if self.dropped:
            logger.warning("firewall: %d events were not recorded (buffer full)", self.dropped)
            self.dropped = 0
        return written

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            try:
                await self.flush()
            except Exception:
                logger.exception(
                    "firewall: recording events failed; they are retried with the next batch"
                )

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="pawabase-firewall-flush")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        try:
            await self.flush()
        except Exception:
            logger.exception("firewall: final flush failed")
