# Platform Maturity Upgrade — Progress

Branch: `feat/platform-maturity`. Audit: `~/sillo.build/pawabase-audit/`. This file records genuine progress only: what is done and tested, what is in progress, and what is deferred with reasons. Nothing listed as done exists only as UI or skeleton. **The maturity upgrade as a whole is not complete** — see the phase tables.

## Phase 1 — Repository verification (2026-10-15)

- Audit findings re-checked against the branch. The branch carries the storage/Studio/Advisor/Table-editor work committed by the user, plus **uncommitted work by another agent (Codex)** on Phase 2 items (SQL console, realtime row authorization, data API correctness). Per the user's decision, that work is the starting point: it is reviewed, completed and tested here rather than redone.
- Codex's uncommitted changes (reviewed, kept, then corrected where noted below):
  - `data/console.py`: one statement per call, parsed with sqlglot; reads executed in a database-enforced read-only transaction. *Its "refuse the console on schema-shared Postgres" guard disabled the console on every default install; replaced by per-environment roles (below).*
  - `data/inspect.py`: `is_read_only` classifies the parsed AST. *It refused `EXPLAIN`/`SHOW`/`DESCRIBE`/`PRAGMA`, which the console supported before, and refused all MySQL reads (503); both restored with database enforcement (below).*
  - `data/presentation.py`: public record shape drops undeclared and `write_only` fields (nested too); used by the REST API, event payloads and realtime.
  - Realtime `resource:*` channels delivered per subscriber after the read policy is checked against the event row and the current row; clients may not publish on `resource:*`; history filtered the same way. *The channel's subscribe gate still used the row policy without a row, so owner-scoped resources could not be subscribed to at all; fixed (below).*
  - Data API: PATCH refuses `null` for non-nullable fields (422); booleans parsed strictly; invalid values → 400; `select` applied after policy evaluation.

## Phase 2 — Security and correctness

| Item | Audit ref | State |
|---|---|---|
| SQL console: AST classification + DB-enforced read-only | 02 §5, 04 | **Done, tested** (Postgres, SQLite, MySQL against real servers) |
| SQL console / function SQL: cross-environment isolation on shared Postgres | 02 §5 | **Done, tested on Postgres 16** (per-environment login roles) |
| Realtime: row-authorized resource notifications | 02 §13 | **Done, tested** |
| Data API: boolean filters, PATCH null, constraint errors, write_only | 02 §4 | **Done, tested** |
| Event delivery reliability | 02 §6 | **Done, tested** (events through the `events` job queue; resumable processor) |
| Durable job retries (no in-worker sleeps) | 02 §8 | **Done, tested** |
| Webhooks honour `max_attempts`, long-horizon retries, bulk redelivery | 02 §7 | **Done, tested** |
| Function sandbox isolation | 02 §10 | **Done, tested on Linux (Docker, kernel 6.12)** — Landlock + seccomp, fail-closed |
| Cloud plan-limit enforcement | 02 §21 | **Done, tested** (`projects/quotas.py`; every stored-but-unenforced limit now enforced) |

### What was built

**Durable events and jobs**
- Every platform event becomes a `ProcessEventJob` on the `events` queue (`automation/jobs/events.py`, `Platform.deliver_event`). The Sillo Redis queue holds a job with a visibility deadline until it is acknowledged, so a worker that dies mid-event leaves it to be taken again (previously `BRPOP` lost it).
- `EventProcessor.handle(strict=True)` is resumable: consumers already handed their work are recorded with a stable key (`type:target:node`) and skipped on retry; `EventLog.routed_at` marks an event that reached all of them. Delivery is at least once; the event id is the idempotency key.
- `PawabaseJob` retries are scheduled in the queue with exponential backoff and jitter (`_attempt`/`_record` carried on the job), instead of sleeping inside the worker. Retries report to the original `JobRun`. An overridable `retrying()` hook.
- Scheduler event targets and storage events go through `emit_event`, so they are durable too.

**Webhooks**
- `DeliverWebhookJob` honours the endpoint's `max_attempts` (1–10, default 5), on a schedule 10 s → 1 m → 5 m → 30 m → 2 h → 6 h → 12 h → 24 h → 24 h (±20 %); `WebhookDelivery.next_attempt_at` shows when the next attempt is due.
- A retried event does not create a second delivery for the same endpoint.
- `POST /platform/v1/envs/{env}/webhook-deliveries/redeliver`: bulk redelivery by ids or by status/endpoint/event/age, with `dry_run`; audited as `webhook.bulk_redelivered`.

**SQL**
- Statements parsed (sqlglot): exactly one query, describing statement (`EXPLAIN`, `SHOW`, `DESCRIBE`, `PRAGMA`), data change or schema change; anything else `400 unsupported_sql`.
- Reads are database-enforced: Postgres `SET TRANSACTION READ ONLY`; MySQL `START TRANSACTION READ ONLY` + rollback (restored — Codex's version refused MySQL reads); SQLite authorizer with an allowlist of inspecting pragmas and read-only settings.
- `data/isolation.py`: on a schema-shared Postgres, developer SQL (console, a function's `db()`/`transaction()`/`db_query`, flows' `db.query`) runs as a per-environment **login role** (`pb_sql_<hash>`, password derived from the master key). It owns its schema's tables (so console DDL works), is granted nothing else, and cannot `SET ROLE`/`set_config('role')` back. Fails closed with `503 sql_isolation_unavailable` if the role cannot be made; `PAWABASE_SQL_ISOLATION=off` opts out.
- `DataSource.connect` creates the schema only when missing (`IF NOT EXISTS` still needs database CREATE, which the role lacks).

**Realtime**
- Resource channels: subscribing is allowed while the resource is readable at all; the row policy is applied to every change per subscriber (as Codex built). Current row fetched once per message, not once per subscriber.

**Function sandbox** (`functions/sandbox/confine.py`)
- Workers confine themselves before importing deployment code. **Landlock**: read Python, platform packages, the deployment and its libraries; write only the worker's temp dir; TCP connect/bind refused (ABI ≥ 4); abstract Unix sockets and signals scoped (ABI ≥ 6). **seccomp** (BPF built in Python, x86_64 and aarch64): `socket()` refused for every family; ptrace, process_vm_*, mount, namespaces (`unshare`, `setns`, namespace `clone` flags; `clone3` → ENOSYS), bpf, perf, keys, modules, kexec, io_uring, pidfd, handle syscalls refused; `kill`/`tgkill` only on itself.
- `PAWABASE_SANDBOX_CONFINEMENT`: `auto` (default: required on Linux, process-only with a warning elsewhere), `required`, `off`. A worker that cannot be confined when required runs nothing (`SandboxConfinementUnavailable`).
- Per-call CPU budget (`RLIMIT_CPU` soft limit = used + timeout) → `CpuLimitExceeded`; workers start in their own session; `MALLOC_ARENA_MAX=2` so threads work under the address-space limit.
- Verified in the Pawabase image on Docker (x86_64, kernel 6.12, Landlock ABI 6, non-root): 22 escape probes (other deployments, `/etc/passwd`, `/proc/1/environ`, TCP/UDP/Unix sockets, signalling the server, ptrace, user namespaces, `/bin/sh`, io_uring) refused; Python, threads, asyncio, stdlib, `exec python` work. The full sandbox suite passes in that container with confinement required.

**Plan limits** (`projects/quotas.py`, migration 0024)
- Counts, project-wide, where things are made: API keys, webhooks, inbound hooks, schedules, environments (manager and management API), backups (manual refused; scheduled/before-restore replace the oldest unpinned), users.
- Identity (limits carried on `AuthConfig`, ≤10 s cache): monthly active users (a user's first session of the month), sessions per user (oldest revoked, min of env and plan), MFA factors per user. Sign-in providers on auth settings save.
- Monthly metering: function invocations, flow runs — `UsageCounter` rows per project/metric/month, seeded from run records, leased per process (≤16 units).
- Storage: `MeteredBucket` (every write path) keeps a stored-bytes gauge (stat before replace/delete), caps the stream at the plan's room; `max_upload_bytes` folds into the upload cap; recount from storage when missing.
- Database bytes: environment size cached 60 s; creates/imports/console inserts refused over the limit, deletes/drops allowed.
- Realtime channels in use per project (per instance).
- Refusal: `403 {"error": "quota_exceeded", "details": {limit, allowed, used}}`. `GET /envs/{env}/plan-usage` reports allowance and use; Studio Usage shows used-of-allowed bars.

## Phase 3 — Shared infrastructure

| Part | State |
|---|---|
| Durable messaging | Events and all platform work through the Sillo Redis queue (visibility timeout, ack, crash recovery), failed jobs land in the failed-job store and can be replayed. A generic message API for customer queues is deferred to Phase 4 Queues, built on this. |
| Retries | **Done.** `core/retry.py` `RetryPolicy` (attempts, base/max or explicit schedule, jitter) drives `PawabaseJob` and webhook delivery; `GET /retry-policies`; Studio Jobs → Retry policies. |
| Scheduling | **Done.** Leader lease (`core/leases.py`, `pb_leases`, migration 0025): any number of schedulers, one fires; per-schedule time zone, `overlap: skip`, `catch_up` of a missed cron slot on taking leadership; Studio "Run now" always runs. Schedule form has the new fields. |
| Quota enforcement | **Done** (above) plus usage reporting. |
| Metrics | **Done.** `observability/service_metrics.py`: one shape for every background service (done, failed, failure rate, retrying, waiting, oldest waiting) from the records services already keep; `GET /envs/{env}/service-health`; Studio Jobs → Service health. |
| Audit events | **Done.** Filters (`action=service.*`, actor, target, env, since/until), `GET /audit/export?format=csv|jsonl`, retention `PAWABASE_AUDIT_RETENTION_DAYS` (365) purged daily by the leading scheduler; Studio Audit page filters and export. |

## Firewall (2026-10-16)

`core/firewall.py` (rules, signatures), `edge/firewall.py` (enforcement), `routes/platform/firewall.py` (API), Studio Networking → Firewall. Migration `0026_advanced_firewall`.
- Applies to every request to an environment's public API, **with or without a key** (keyless routes resolve their environment from the hostname or the path, never from a header the caller sets); the management plane is exempt.
- Rules: conditions on ip, country (trusted proxy header `PAWABASE_FIREWALL_COUNTRY_HEADER`), method, path, host, query, `query:<name>`, `header:<name>`, user agent, origin, referer, key role, key id, body size; 16 operators incl. regex, CIDR, lists, numbers; all/any. Actions allow / block / log / rate limit (by ip, key, ip+path or a header; optional ban); first allow or block decides, as before; simulate mode; expiry; hit counts.
- Managed signatures: SQL injection, XSS, path traversal, scanners, sensitive-file probes, missing user agent, unusual methods — each off / log / block.
- Bans: manual (address or range, timed or permanent), from rate limits, and automatic (N error responses in a window; exempt ranges). Allow rules beat bans.
- Events batched to `pb_firewall_events` (retention `PAWABASE_FIREWALL_EVENT_RETENTION_DAYS`, 14); stats with timeline and top addresses/paths/rules/countries; step-by-step request tester.
- Verified live: a keyless SQL-injection request was refused at the gateway and recorded. Full suite 744 passed.

## TypeScript functions, user administration and the SDKs (2026-10-16)

- **TypeScript functions** run in a Deno worker confined by Landlock + seccomp and then Deno permissions (`functions/sandbox/{typescript,deno_launch,confine}.py`, `deno/{pawabase,worker}.ts`); one language per project. Functions import the kit as `"@pawabase/functions"` or `"pawabase"` (same module). Confinement tests need the Linux image.
- **User administration through the gateway.** `/admin/v1` is blocked publicly, so a customer's secret key could not administer its users. The identity admin and directory routes are now also mounted at `/platform/v1/envs/{env}/auth/…` behind the same `MANAGE` gate (own environment only); the internal `SERVICE_ONLY` mount is unchanged. `tests/platform/test_user_admin_routes.py` (3).
- **SDKs, written by hand and split into packages** (separate repos, branch `feat/sdk-split`): Python `pawabase` (client, `pb.manage`, `pb.admin`, realtime) and `pawabase-functions` (kit, CLI, emulator) share one `pawabase/` namespace; JS `@pawabase/client` (+ `/manage`, `/admin` subpath imports) and `@pawabase/functions` (kit, `/testing`, `pawabase-functions` CLI). Tests: Python client 43 + 5 live-gated, Python functions 57, JS client suite green, JS functions 24. The TypeScript kit file in the npm package is identical to `deno/pawabase.ts` here; **keep them in sync**.
- Not verified: the SDK live suites (`PAWABASE_LIVE_*`) were not run against the Docker stack after the user-admin routes landed (needs a rebuilt `pawabase` image); `pawabase-functions deploy` has been tested against a fake gateway and its bundle read back with Python's `tarfile`, not yet deployed to a running gateway.

## Functions runtime: HTTP functions and the runtime service (2026-10-17)

Supabase-style functions as a service of its own. `functions/service/` (engine, pools, grants, bridge, egress, service app, gateway), `functions/http_functions.py`, `functions/http_dispatch.py`, `functions/vendor.py`, `deno/http_worker.ts`, `deno/http_support.ts`, kit `serve()`/`getContext()`/`sse()`; migration `0027_http_functions`.
- **HTTP functions** (`functions/<name>/index.ts`): any method and path under `/functions/v1/<name>`, `serve()` or `Deno.serve`, streamed both ways, `waitUntil`/`EdgeRuntime.waitUntil`, secrets as `Deno.env`, per-function `config` (auth `key`/`user`/`service`/`public`/policy, methods, timeout, memory, warm, concurrency, origins), keyless `/functions/v1/public/<env>/<name>` for `auth: "public"`. `fn()` functions unchanged.
- **Runtime service** (`python -m pawabase_server.functions.service`, `functions` container, default in compose) or embedded (empty `PAWABASE_FUNCTIONS_RUNTIME_URL`). The service has no DB/Redis credentials and no secrets; it acts through signed per-invocation **grants** (`/internal/v1/functions/rpc/*`, `/report`, `/variables`). `fn()` calls (Python and TS) run there too.
- **Workers**: per HTTP function, concurrent, scale out to `functions_max_workers`, queue then 503, warm workers, draining on deploy/secret change, crash isolation, stats (p50/p95/p99, cold starts, rejections, timeouts).
- **Egress**: `fetch()` proxied through the runtime with a DNS-rebinding-safe guard (address pinned at connect), redirect re-checks, credential stripping, per-invocation budgets. `direct` network mode keeps sockets (seccomp/Landlock network relaxed only).
- **Vendoring**: `npm:`/`jsr:`/URL imports fetched at activation into `.deno/<digest>`, workers `--cached-only`; import maps from `functions/deno.json` and per-function `deno.json` (cannot escape the deployment or replace the kit).
- **Management**: `/functions-runtime` (status), `/functions-runtime/restart`, `/http-functions/<name>` (stats), `/http-functions/<name>/request` (Try it); `/functions` lists HTTP functions (`kind: "http"`). Studio: HTTP badge, facts, Performance tab (stats + workers + restart), HTTP "Try it".
- **SDKs**: JS `functions.fetch()`, Python `functions.request()`; `@pawabase/functions` `serve()` kit, `callHttp`/`loadHttpFunction` test helpers, `new`, `serve` (local Deno dev server); Python CLI scaffolds HTTP functions.
- Verified: 17 Deno end-to-end tests on Linux with confinement (incl. the service path over ASGI and the vendoring test against real npm/jsr), 29 unit tests, live dev stack with the `functions` container (Cloud's Python functions through the service). Not verified: WebSocket upgrades in HTTP functions (not supported yet), per-request CPU-time limits (wall clock and heap only), multi-instance service behind a load balancer.

## Tests executed

| Suite | Result |
|---|---|
| Full suite (macOS, before isolation/sandbox work) | 689 passed, 4 skipped |
| Full suite (macOS, with Postgres opt-in tests) | 718 passed, 3 skipped, 1 failed — `test_ulid_migration::test_a_fresh_postgres…`, **pre-existing**: it asserts the project/org tables are absent, which the 2026-10-13 projects feature re-added; it only runs with `PAWABASE_TEST_POSTGRES_URL` |
| `tests/platform/test_durable_delivery.py` (new, 7) | pass — retry scheduling, settle at limit, resumable routing without duplicates, events as jobs, webhook schedule/`max_attempts`/`next_attempt_at`, dedupe, bulk redelivery, Redis visibility-timeout recovery (fakeredis) |
| `tests/platform/test_data_correctness.py` (new, 20) | pass — booleans, PATCH null, 409 unique, write_only, AST classification, console refusals, describing statements |
| `tests/platform/test_sql_isolation.py` (new, 7; needs `PAWABASE_TEST_POSTGRES_URL`) | pass on Postgres 16 — own schema only, no role switch, DDL + platform access, DB read-only incl. `EXPLAIN ANALYZE DELETE`, opt-out, fail-closed |
| `tests/platform/test_console_mysql.py` (new, 2; needs `PAWABASE_TEST_MYSQL_URL`) | pass on MySQL 8.4 |
| `tests/platform/test_realtime_rows.py` (new, 3) | pass — per-subscriber rows, write-only never sent, history after lost access, no client publish |
| `tests/platform/test_plan_limits.py` (13) | pass |
| `tests/platform/test_scheduler_lease.py` (3), `test_service_health_audit.py` (2) | pass |
| `tests/platform/test_sandbox_confinement.py` (new, 6) + `test_sandbox.py` | pass on Linux (Docker); macOS runs the fail-closed and builder tests |

## Migrations

- `0024_usage_counters`: `pb_usage_counters` (new table).
- `0025_scheduler_lease`: `Schedule.timezone/overlap/catch_up`, `pb_leases` (new table). Additive.
- `0023_durable_events`: `pb_event_log.routed_at` (backfilled from `created_at`), `pb_webhook_deliveries.next_attempt_at`. Additive; no destructive change.

## Deferred / outstanding

- **Phase 4** (service maturity) and **Phase 6** (integration verification) — not started. **Phase 5** (Studio) partly done: page tabs replaced by secondary sidebars; Functions detail view with Try it; Webhooks one primary item with Endpoints / Deliveries / Inbound hooks.
- Plan limits: definitions arriving by promotion or blueprint import are not counted against the plan at copy time; realtime channel limit is per instance.
- Sandbox: a function cannot start helper subprocesses other than Python, nor signal its own children (`kill` limited to itself). No per-function CPU/memory settings yet. On macOS (development) functions are process-isolated only.
- SQL isolation: MySQL project databases sharing one server rely on the configured MySQL user's grants; only Postgres schema-sharing gets per-environment roles. The Postgres catalogue (names of other schemas) stays readable, as on any Postgres.
- Studio does not yet expose bulk redelivery or `next_attempt_at` (API only).
