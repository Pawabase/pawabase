# Pawabase Architecture

Pawabase is a self-hostable backend platform built on [Sillo](https://github.com/sillohq/core).
The developer brings the infrastructure (database, Redis, object storage, SMTP, OAuth
credentials). Pawabase provides the platform around it: identity, policies, resources and
APIs, realtime pub/sub, storage, events, queues, jobs, scheduling, caching, flows,
webhooks, secrets, documentation and observability, all operated from Studio.

The one rule behind every decision is **Sillo first**: if Sillo (or a first-party Sillo package)
ships the primitive, Pawabase uses it and adds the product around it.

The second rule is **one server, and Studio beside it**. Everything the backend does is one Python
package, `pawabase_server`, running as one process with one database: no internal services and no
network between its parts. Studio, the control plane, is its own service (the top-level `studio`
package, its own container) and manages the server over HTTP, as any other client of the management
API would.

---

## 1. Two services

```
        clients (web, mobile,                you, in a browser
        servers, SDKs, the CLI)                      │
                  │                                  │
          :8080 public                         :8090 Studio
     ┌────────────▼───────────┐             ┌────────▼────────┐
     │  edge                  │             │  studio          │
     │  routes · API keys ·   │             │  Sillo + Inertia │
     │  firewall · CORS ·     │             │  no sign-in      │
     │  rate limits · quotas  │             │  unless set      │
     └────────────┬───────────┘             └────────┬────────┘
                  │ signed context                   │ service token, HTTP
                  │ (in-process)                     │
     ┌────────────▼──────────────────────────────────▼──────┐
     │  pawabase_server: one Sillo app, one Platform object   │   :8001 internal
     │  identity · management plane · data plane · storage ·  │   (not published)
     │  flows · events · realtime                             │
     │  + queue worker and scheduler, as tasks of this process │
     └────────────────────────────┬──────────────────────────┘
                                  │
                   Postgres · Redis (optional) · S3-compatible storage
```

| Service | Where | What it is |
|---|---|---|
| **Server** | `pawabase_server` (`python -m pawabase_server`) | The application (`pawabase_server.app.create_app`) and its public entry point (`create_public`: the application behind the edge). It serves the public listener on `:8080` and, from the same process, the application itself on an internal listener, `:8001`. |
| **Studio** | `studio` (`python -m studio`) | The control plane. It reaches the server over the internal listener with short-lived service tokens, holds no credential of its own, and is published on its own port. |

The two ship in one image with two commands (`serve`, `studio`) and run as two containers.

### Why the internal listener exists

Studio has no sign-in of its own by default (it manages the one backend it runs beside), so it
must never be reachable through the public entry point. The edge enforces that by construction: it
answers only the paths in `pawabase_server.edge.routing`, and `/internal`, `/admin` and Studio's
own paths are never among them. The internal listener is the same application without the edge, for
services that authenticate with service tokens; it is never published, only reachable on the
private network, and every route that matters there requires a service token.

### Layout

The package is organised by what a module is for, not by which part of the product it came from.

| Folder | What lives there |
|---|---|
| *(root)* | `app` (assembly), `server` (listeners), `cli`, `settings`, `platform` (the long-lived services) |
| `core/` | reusable foundation: `ids`, `crypto`, `tokens`, `context`, `principal`, `auth`, `errors`, `failures`, `envvars`, `dburl`, `ratelimit`, `buckets`, `firewall`, `money`, `templating`, `telemetry`, `events`, `clients`, `asgi`, `service`, ... |
| `db/` | `database` (connection and commands), `fields`, `models/` (every table), `migrations/` (one chain) |
| `identity/` | `accounts` (the shared service and user accounts), `sessions`, `mfa`, `oauth`, `rbac`, `invitations`, `links`, `emails`, `environment` (auth settings), `backup` |
| `environments/` | `state`, `env_settings`, `configuration`, `blueprints`, `releases`, `reseal`, `export_config` |
| `compiler/` | resources and routes compiled into Sillo routes; `dispatch` hands `/rest/<version>` to a released app |
| `data/` | SQL across SQLite, PostgreSQL and MySQL; what happens after a record changes |
| `automation/` | `worker`, `scheduler`, `event_processor`, `webhooks`, `jobs/` |
| `observability/` | `analytics`, `insights`, `metrics`, `request_metrics`, `route_stats`, `system_health`, `alarms`, `capacity`, `status` |
| `backup/` | `backups`, `snapshots` |
| `edge/` | `pipeline` (the ASGI middleware), `routing`, `key_resolver`, `policy`, `admission`, `quotas`, `limits`, `networking`, `clientip`, `headers` |
| `realtime/` | channels, publication, presence, fan-out; `protocol/` is what clients may send |
| `flows/`, `policies/` | the flow engine and its blocks; the policy engine |
| `infra/` | `mail`, `outbound`, `storage/` (Sillo storage and an S3-compatible driver) |
| `routes/` | HTTP routes: `platform`, `data`, `identity`, `realtime`, `public` |

The public Kit and CLI (`pawabase`) live in Pawabase/pawabase-python and are a dependency, which is
why the package is not called `pawabase`.

---

## 2. Requests and identity

### 2.1 Public surface

One origin. Nothing about the inside leaks into client code:

| Public path | Served by |
|---|---|
| `/auth/v1/*` | identity (`routes/identity`) |
| `/rest/v1/*` | the data plane: resources, custom routes, extension routes |
| `/storage/v1/*` | storage |
| `/flows/v1/*` | flow invocation (HTTP-triggered flows) |
| `/hooks/v1/*` | inbound webhooks |
| `/realtime/v1/*` | realtime (HTTP and WebSocket) |
| `/docs/v1/*` | Atlas documentation for an environment |
| `/platform/v1/*` | the management plane (CLI, automation; Studio uses it over the internal listener) |
| `/`, `/health`, `/status`, `/v1/status` | the edge itself |

### 2.2 Credentials

* **API keys.** Keys come in two roles: `publishable` (`pk_…`, safe in browsers, anonymous
  role, subject to policies) and `secret` (`sk_…`, server-side, the `service` role). A key belongs to one
  **environment**, carries optional scopes and expiry, and is revocable. Keys are
  generated and hashed with Sillo's `sillo.auth.apikey.generate_api_key` / `hash_api_key`.
* **User access tokens.** Issued by identity to your application's users. Each environment
  has its own signing key, derived from the platform master secret (`HMAC(master, "<env>")`), so
  tokens from one environment can never be accepted by another.
* **Studio** needs no credential of its own beyond the shared internal secret: it calls the server
  with short-lived service tokens. The CLI and automation use a scoped secret API key.

### 2.3 Context

The edge resolves the `apikey` (cached with Sillo's cache, single-flight, tolerant of a brief
database failure) and hands the request to the application with an **`X-Pawabase-Context`**
header: a short-lived JWT signed with the internal secret, carrying `{env, key_id, role, scopes}`.
The edge first strips anything a client sent under `X-Pawabase-*`, so the application trusts only
this signed header, never a raw key, and verifies it with `ContextBackend`, a Sillo
`AuthenticationBackend`.

User tokens travel untouched in `Authorization: Bearer …` and are validated statelessly with
`UserBackend` using the environment's derived key. Studio's calls to the server use
**service tokens** validated by `ServiceBackend`.

Every request carries Sillo's request id (`RequestIdMiddleware`), minted at the edge, so one
request can be followed through telemetry in Studio.

### 2.4 What happens in-process

Parts of the server call each other as Python, not HTTP: identity reads an environment's auth
settings, sends mail and publishes events through `Platform`; realtime reads channel rules and
forwards publications to flows through it; the management plane reads and writes roles and
identity backups through `rbac` and `identity_backup`; the edge looks keys up through
`key_resolver`. Studio is the exception on purpose: it is a client of the management API, in
the same process (`ServiceClient(app=...)`, an ASGI transport with no socket), so what it can
do is exactly what the API allows.

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
* **The edge** is ASGI middleware (screening, key policy, admission, signed context), because it
  has to decide before the application sees a byte and must treat HTTP and WebSocket alike.

---

## 4. One runtime, its environments, infrastructure

**One Pawabase installation is one project.** There is no list of projects to pick from, no
organization above it and no team to invite: you start Pawabase and open Studio, and you are
managing that backend. The surrounding hierarchy (Cloud user, organization, team, projects) is the
business of Pawabase Cloud, which will run many of these runtimes and sit entirely outside this
code. Core does not know it exists, so a runtime can later be provisioned and managed remotely
without Core changing.

Studio has no sign-in for the same reason: it manages the runtime it is deployed with. It acts on the
application with service tokens, never as a person, and the Docker install publishes it on
localhost only. Put an authenticating proxy in front of it, or tunnel to it, before exposing it. The
management API is reached by Studio, or by the CLI with a scoped secret API key.

What is *not* removed is **application Auth**: identity still serves the users of the application you
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

**Object delivery.** Objects are served by the application gateway, never by the storage provider. Every object has
a stable URL, `/storage/v1/files/<env>/<bucket>/<key>`, a keyless route (the environment is in the path, like the other
browser-navigation routes, and a hostname mapped to one environment refuses another's). All reads (`/object`, `/files`,
`/share`, legacy `/signed`) share `infra/storage/delivery.py`: content type from sniffing, inline only for safe types,
ranges, validators, gzip for text and `infra/storage/transform.py` image variants (bounded dimensions, size, time and
concurrency; cached by object version and options; the read policy is checked first). Policies are per action (read,
upload, list, replace, delete). Studio manages objects through `/platform/v1/envs/{env}/buckets/{bucket}/object*` with its
service credential, outside the bucket's application policies. Temporary links (`ObjectShare`, `infra/storage/shares.py`)
are looked up by token hash, name one object, expire and can be revoked.

Values of the form `secret://NAME` are resolved from the environment's encrypted secrets when they
are used. They are never returned by the API.

---

## 5. Resources and the data plane

A **Resource** is an application entity plus its behaviour: fields, relationships, which
operations are exposed, per-operation policies, request and response schemas, a transformer,
cache TTL, rate limit, event publication, realtime publication and documentation.

On change, the platform **compiles** an environment into a `SilloApp`:

* each exposed operation (`list`, `get`, `create`, `update`, `delete`) becomes a Sillo route whose
  `request_model` and `response_model` are generated from the Resource's fields;
* each custom route becomes a Sillo route whose handler runs a Flow;
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
`env`). Templates are lookups, not a language.

Blocks are discovered from the `pawabase.blocks` entry-point group, so third-party packages can
add blocks. The built-in set (40+) covers HTTP, auth, policies, validation, resources, raw
queries, transactions, conditions, switches, loops, cache, queues, jobs, events, realtime,
storage, mail, webhooks, external HTTP, secrets, delays, retries, transformations, logging, metrics, responses and errors.

---

## 7. Events

Any part of the server publishes domain events (`user.created`, `order.paid`, `file.uploaded`,
`job.completed`, …) through the kit's `EventBus`, which wraps Sillo's `EventEmitter`:

* `persistent` (Redis list, at-least-once) carries platform events to the event processor;
* `memory` is used in tests and single-process development.

The event processor records every event (origin service, payload, consumers, outcomes) for
inspection, then fans out to subscriptions: flows, jobs, outbound webhooks and
realtime publication.

## 8. Realtime

Realtime means application pub/sub. Clients connect to `/realtime/v1/socket` with an API key and
optionally a user token, then `subscribe`, `unsubscribe`, `publish`, `presence` and `history` on
channels. Channels are `sillo-wire` rooms. Channel access is decided by Pawabase policies
matched by channel pattern (`chat:*`, `user:{auth.user_id}`). Servers publish over HTTP. Multiple
Server instances share traffic through Sillo's Redis `EventEmitter`.

**Resource subscriptions** are one use of this: a Resource with realtime enabled publishes
`resource:<name>` channel messages on create, update and delete.

---

## 9. Observability

The server installs a `Telemetry` installable. It records each request (request id,
route, status, duration, environment, identity, credential role, cache outcome, events
emitted, jobs dispatched, error) and exposes it on an internal endpoint that Studio reads, alongside subsystem statistics: cache hit rates, queue depths and
in-flight jobs, failed jobs, scheduler runs, event deliveries, webhook deliveries, realtime
connections and presence.

---

## 10. Extending Pawabase

* **Sillo routers**: `code/routes.py` exporting `router = Router(...)`, served under
  `/rest/v1/x/…`. Handlers call `pawabase_server.extend.authorize(ctx, policy)` for the platform's
  policy check and a runtime (data, events, secrets, outbound HTTP) acting for the caller. Mounted
  code is trusted: it runs in the server process, so it is for the operator's own code.
* **Python policies and transformers**: `@policy`, `@transformer`.
* **Blocks**: any installed package exposing a `pawabase.blocks` entry point.
* **Storage and OAuth providers**: Sillo storage drivers and `sillo-oauth` `OAuthProvider`s.
