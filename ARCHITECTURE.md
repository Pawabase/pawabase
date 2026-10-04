# Pawabase Architecture

Pawabase is a self-hostable backend platform built on [Sillo](https://github.com/sillohq/core).
The developer brings the infrastructure (database, Redis, object storage, SMTP, OAuth
credentials). Pawabase provides the platform around it: identity, policies, resources and
APIs, realtime pub/sub, storage, events, queues, jobs, scheduling, caching, functions, flows,
webhooks, secrets, documentation and observability, all operated from Studio.

This document explains how the platform is divided and how each capability maps onto Sillo.
The one rule behind every decision is **Sillo first**: if Sillo (or a first-party Sillo package)
ships the primitive, Pawabase uses it and adds the product around it.

---

## 1. Services

```
                        ┌────────────────────────────┐
  clients (web, mobile, │        API Gateway          │  :8080  public entry point
  servers, Studio UI) ──▶  routing · key resolution   │
                        │  signed context · CORS · RL │
                        └──┬─────────┬─────────┬──────┘
                           │         │         │
          ┌────────────────▼──┐ ┌────▼──────┐ ┌▼──────────────┐
          │   Pawabase API    │ │  Akountz  │ │    Angula     │
          │ resources·routes │ │ identity  │ │ realtime      │
          │ routes·flows·jobs │ │ OAuth·MFA │ │ pub/sub       │
          │ storage·events…   │ │ orgs·RBAC │ │ presence      │
          └──┬──────┬─────────┘ └───────────┘ └───────────────┘
             │      │  same image, separate processes
     ┌───────▼─┐ ┌──▼────────┐                 ┌───────────────┐
     │ worker  │ │ scheduler │                 │    Studio     │ :8090 control plane
     └─────────┘ └───────────┘                 │ Sillo+Inertia │ (talks to services
                                               └───────────────┘  with service tokens)
```

| Service | Directory | Owns |
|---|---|---|
| **Gateway** | `gateway` | Public routing, API-key resolution, signed platform context, CORS, rate limiting, request ids, WebSocket proxying. No business logic. |
| **Pawabase API** | `api` | Environments, the runtime-configuration authority, Resources and the data plane, custom routes, schemas, transformers, policies, functions, flows, events, webhooks, queues, jobs, scheduler, cache, storage, secrets, API keys, mail, Atlas docs. |
| **Worker** | `api` (`python -m app.worker`) | Runs Sillo `QueueWorker`s: flows, functions, webhook delivery, mail, event processing. |
| **Scheduler** | `api` (`python -m app.scheduler`) | Runs Sillo's `SchedulerManager`, loading schedules from the database. |
| **Akountz** | `akountz` | Identity: users, passwords, tokens, sessions, verification, reset, magic links, OAuth/OIDC, MFA (TOTP), recovery codes, your application's organizations, teams and invitations, roles, permissions, login history. |
| **Angula** | `angula` | Realtime: channels, topics, publish/subscribe, presence, channel authorization, inspection. |
| **Studio** | `studio` | The control plane UI (Sillo + `sillo-inertia` + React). |
| **Kit** | `pawabase_core/` | Shared code, not a service or an installable package: policy engine, flow engine and blocks, schema compiler, transformers, service authentication, context propagation, telemetry and service bootstrap. |

The worker and scheduler run the API image as separate processes because they have separate
scaling and failure characteristics (Sillo's docs say not to run the scheduler in every web
process). They are not separate services, since they own no separate domain.

---

## 2. Requests and identity

### 2.1 Public surface

The gateway presents one origin. Internal topology never leaks into client code:

| Public path | Upstream |
|---|---|
| `/auth/v1/*` | Akountz |
| `/rest/v1/*` | API data plane (resources, custom routes, extension routes) |
| `/storage/v1/*` | API storage |
| `/functions/v1/*` | API function invocation |
| `/flows/v1/*` | API flow invocation (HTTP-triggered flows) |
| `/hooks/v1/*` | API inbound webhooks |
| `/realtime/v1/*` | Angula (HTTP and WebSocket) |
| `/docs/v1/*` | API: Atlas documentation for an environment |
| `/platform/v1/*` | API management plane (Studio, CLI, automation) |

### 2.2 Credentials

* **API keys.** Keys come in two roles: `publishable` (`pk_…`, safe in browsers, anonymous
  role, subject to policies) and `secret` (`sk_…`, server-side, the `service` role). A key belongs to one
  **environment**, carries optional scopes and expiry, and is revocable. Keys are
  generated and hashed with Sillo's `sillo.auth.apikey.generate_api_key` / `hash_api_key`.
* **User access tokens.** These are issued by Akountz to your application's users. Each environment
  has its own signing key, derived from the platform master secret (`HMAC(master, "<env>")`), so
  tokens from one environment can never be accepted by another.
* **Studio** needs no credential of its own: it calls the other services with short-lived service
  tokens. The CLI and automation use a scoped secret API key.

### 2.3 Context propagation

The gateway resolves `apikey` against the API (the result is cached with Sillo's cache) and forwards
the request with an **`X-Pawabase-Context`** header: a short-lived JWT signed with the internal
secret, carrying `{env, key_id, role, scopes}`. Internal services trust only this
signed header, never a raw key, and verify it with the kit's `ContextBackend`, a Sillo
`AuthenticationBackend`.

User tokens travel untouched in `Authorization: Bearer …`. Any service validates them statelessly
with the kit's `UserBackend`, another Sillo `AuthenticationBackend`, using the environment's
derived key.

Service-to-service calls (Studio → API/Akountz/Angula, API → Angula/Akountz) use short-lived
**service tokens** validated by the kit's `ServiceBackend`.

Every hop carries Sillo's request id (`RequestIdMiddleware`), so one request can be followed
across services in Studio.

---

## 3. Sillo mapping

| Pawabase capability | Sillo / Sillo package used |
|---|---|
| HTTP routing, validation, DI | `SilloApp`, `Router`, `request_model`/`response_model`, `Depend`, parameter markers |
| Per-project APIs | A **compiled `SilloApp` per environment**: resources and custom routes become real Sillo routes with Pydantic request/response models, so validation and OpenAPI come from Sillo |
| API documentation | Sillo OpenAPI generation, rendered with Sillo's `Atlas` UI |
| Auth backends and gates | `AuthenticationBackend`, `SilloApp(auth=[…])`, `useAuth` (the policy gate subclasses `useAuth`) |
| Users and passwords | `sillo.users.UserBaseModel`, `UserManager`, Sillo hashing |
| Tokens, refresh, rotation, theft detection | `sillo.auth.jwt_auth.JWTUserMixin`, `JWTToken`, `TokenForUser` |
| Browser sessions (Studio) | `SessionMiddleware`, `SessionAuthBackend`, `login`/`logout` |
| API keys | `sillo.auth.apikey` key generation and hashing |
| Roles and permissions | `sillo.permissions` (`Permission`, `Group`, `UserPermission`, `UserGroup`) |
| Social login / OAuth / OIDC | `sillo-oauth` (`authorize_url`, `exchange`, shipped and custom providers) |
| Signed one-time links | `sillo.helpers.signing.URLSafeTimedSerializer` |
| Secrets encryption | `sillo.helpers.crypto` (Fernet) |
| Database | `sillo.record` (`Record` installable, `DatabaseConfig`, models, migrations) |
| Resource data in the developer's DB | Tortoise clients (Sillo Record's engine) opened per environment; SQL is built with PyPika, the query builder Tortoise uses |
| Events | `sillo.events.EventEmitter` (`memory`, `redis`, `persistent`) |
| Queues, jobs, workers | `sillo.work.queue` (`Job`, `RedisConnection`, `QueueWorker`, `FailedJobRepository`, job middleware) |
| Scheduling | `sillo.work.scheduler.SchedulerManager`, `CronTrigger`, `IntervalTrigger` |
| Background tasks | `sillo.work.background.BackgroundTask` |
| Cache | `sillo.cache` (`MemoryCache`, `RedisCache`, tags, stats) |
| Storage | `sillo.storage` (`Storage`, `Bucket`, local/S3/memory drivers, signed URLs, sniffing, storage events); Pawabase policies plug in as a Sillo storage policy |
| Mail | `sillo.mail` (`MailClient`, `MailConfig`) |
| Rate limiting | `sillo.security.RateLimit` (memory/Redis backends) |
| CORS, security headers | `CORSMiddleware`, `Shield` |
| Request ids | `sillo.http.RequestIdMiddleware` |
| Outbound HTTP | `sillo.http.client.HTTPClient` |
| Retry and backoff | `sillo.helpers.retry` |
| Realtime rooms, presence, fan-out, replay | `sillo-wire` (`Hub`, `Peer`, backlog); cross-instance fan-out through Sillo's Redis `EventEmitter` |
| Studio UI | `sillo-inertia` (`Inertia`, `render`, Vite React) |
| Tests | `sillo.testclient.AsyncTestClient` |

### Where Pawabase adds code, and why

Pawabase adds code only where Sillo has no primitive:

* **Policy engine**: declarative, reusable ABAC rules evaluated against auth, record, request and
  project context. Sillo provides the enforcement points (`useAuth`, storage policies); Pawabase
  provides the rules.
* **Flows and blocks**: a visual composition engine whose blocks call Sillo capabilities.
* **Schema compiler**: turns stored JSON schemas into Pydantic models so that Sillo validates them.
* **Transformers**: declarative response shaping.
* **TOTP**: Sillo has no one-time-password primitive, so `pyotp` is used.
* **Job tracking and a database-backed failed-job repository**: implementations of Sillo's
  `FailedJobRepository` interface and Sillo job middleware, so Studio can show queue history.
* **Gateway proxying** uses `httpx` streaming directly. Sillo's `HTTPClient` returns parsed
  bodies, while a proxy has to pass raw streamed bytes and headers through.

---

## 4. One runtime, its environments, infrastructure

**One Pawabase installation is one project.** There is no list of projects to pick from, no
organization above it and no team to invite: you start Pawabase and open Studio, and you are
managing that backend. The surrounding hierarchy (Cloud user, organization, team, projects) is the
business of Pawabase Cloud, which will run many of these runtimes and sit entirely outside this
code. Core does not know it exists, so a runtime can later be provisioned and managed remotely
without Core changing.

Studio has no sign-in for the same reason: it manages the runtime it is deployed with. It acts on the
API, Akountz and Angula with service tokens, never as a person, and the Docker install publishes it on
localhost only. Put an authenticating proxy in front of it, or tunnel to it, before exposing it. The
management API is reached by Studio, or by the CLI with a scoped secret API key.

What is *not* removed is **application Auth**: Akountz still serves the users of the application you
build (sign-up, sessions, OAuth, MFA, roles) and the organizations and teams *those* users create.
Those belong to the application; they are unrelated to administering Pawabase.

A runtime has one or more **environments** (`development`, `production`, …), the unit of isolation.
Everything the platform stores is keyed by environment, and environments never share data, keys,
secrets, signing keys or queues. A fresh runtime starts with `development`. What the runtime calls
itself (Studio's title, API docs, emails) is `PAWABASE_PROJECT_NAME`.

Each environment's infrastructure is configured by the developer and stored on the environment:

| Setting | Meaning |
|---|---|
| `database_url` | The developer's database, where Resource data lives |
| `redis_url` | Cache, queues and events for this environment (optional) |
| `storage` | Driver (`local`, `s3`) plus bucket, endpoint and credentials (credentials are secret references). Unset, the environment uses the platform default below |
| `mail` | SMTP settings (the password is a secret reference) |
| `auth` | Token lifetimes, password policy, enabled providers and their credentials (secret references) |

**Default storage.** An environment that configures no `storage` gets the platform default, set
by `PAWABASE_STORAGE_*`. The Docker install runs MinIO and points the default at it, so files work
with no setup; setting `PAWABASE_STORAGE_ENDPOINT` (and credentials) swaps in any S3-compatible
service. With no endpoint at all (the API run outside Docker) the default is the local disk. The
API creates the remote bucket on startup or on the first write, and every Pawabase bucket is a key
prefix (`<env>/<bucket>/`) inside that one remote bucket. Presigned URLs are signed for
`PAWABASE_STORAGE_PUBLIC_ENDPOINT`, the address browsers reach, which differs from the internal
`http://minio:9000` the API talks to.

Values of the form `secret://NAME` are resolved from the environment's encrypted secrets when they
are used. They are never returned by the API.

---

## 5. Resources and the data plane

A **Resource** is an application entity plus its behaviour: fields, relationships, which
operations are exposed, per-operation policies, request and response schemas, a transformer,
cache TTL, rate limit, event publication, realtime publication and documentation.

On change, the API **compiles** an environment into a `SilloApp`:

* each exposed operation (`list`, `get`, `create`, `update`, `delete`) becomes a Sillo route whose
  `request_model` and `response_model` are generated from the Resource's fields;
* each custom route becomes a Sillo route whose handler runs a Flow or a Python function;
* the runtime's Python extension router (normal Sillo code) is mounted;
* policies are enforced through a `useAuth` subclass on every route;
* Sillo generates the OpenAPI document, and Atlas renders it.

Nothing is public by default: a Resource exposes no operations until the developer enables them,
and every enabled operation is governed by a policy (default: `authenticated`).

List operations support filtering (`?filter[status]=eq.active`), sorting (`?sort=-created_at`) and
pagination (`?page=2&per_page=50`). Policies that compare record fields with auth values are
pushed down into SQL, so pagination stays correct.

---

## 6. Flows and blocks

A Flow is a graph of blocks (stored in the `@xyflow/react` shape so that Studio edits it
directly). It is triggered by an HTTP route, a resource operation, an event, a realtime event, a
schedule, a job, an inbound webhook or a manual run. Block configuration can reference flow
state with `{{ path }}` templates (`input`, `auth`, `vars`, `steps.<id>.output`, `secrets`,
`env`). Templates are lookups, not a language: logic that needs a language uses a Python
function block.

Blocks are discovered from the `pawabase.blocks` entry-point group, so third-party packages can
add blocks. The built-in set (40+) covers HTTP, auth, policies, validation, resources, raw
queries, transactions, conditions, switches, loops, cache, queues, jobs, events, realtime,
storage, mail, webhooks, external HTTP, secrets, delays, retries, transformations, Python
functions, logging, metrics, responses and errors.

---

## 7. Events

Any service publishes domain events (`user.created`, `order.paid`, `file.uploaded`,
`job.completed`, …) through the kit's `EventBus`, which wraps Sillo's `EventEmitter`:

* `persistent` (Redis list, at-least-once) carries platform events to the API's event processor;
* `memory` is used in tests and single-process development.

The event processor records every event (origin service, payload, consumers, outcomes) for
inspection, then fans out to subscriptions: flows, functions, jobs, outbound webhooks and
realtime publication.

## 8. Realtime (Angula)

Realtime means application pub/sub. Clients connect to `/realtime/v1/socket` with an API key and
optionally a user token, then `subscribe`, `unsubscribe`, `publish`, `presence` and `history` on
channels. Channels are `sillo-wire` rooms. Channel access is decided by Pawabase policies
matched by channel pattern (`chat:*`, `user:{auth.user_id}`). Servers publish over HTTP. Multiple
Angula instances share traffic through Sillo's Redis `EventEmitter`.

**Resource subscriptions** are one use of this: a Resource with realtime enabled publishes
`resource:<name>` channel messages on create, update and delete.

---

## 9. Observability

Every service installs the kit's `Telemetry` installable. It records each request (request id,
route, status, duration, environment, identity, credential role, cache outcome, events
emitted, jobs dispatched, error) and exposes it on an internal endpoint. Studio merges these
records across services, alongside subsystem statistics: cache hit rates, queue depths and
in-flight jobs, failed jobs, scheduler runs, event deliveries, webhook deliveries, realtime
connections and presence.

---

## 10. Extending Pawabase

* **Python functions**: `code/functions/*.py`, decorated with `@function`.
* **Sillo routers**: `code/routes.py` exporting `router = Router(...)`, served under
  `/rest/v1/x/…`.
* **Python policies and transformers**: `@policy`, `@transformer`.
* **Blocks**: any installed package exposing a `pawabase.blocks` entry point.
* **Storage and OAuth providers**: Sillo storage drivers and `sillo-oauth` `OAuthProvider`s.
