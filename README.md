# Pawabase

**Bring your infrastructure, build your backend.**

Pawabase is a self-hostable backend platform built on [Sillo](https://github.com/sillohq/core).
You bring the database, Redis, object storage, SMTP and OAuth credentials; Pawabase gives you
the backend around them, operated from Studio. **One Pawabase installation is one project**:
start it, open Studio, and you are managing that backend. There is no sign-in, no organization
and no project to create first.


- **Resources**: tables exposed as REST with filtering, sorting, relations and OpenAPI docs.
- **Policies**: JSON conditions, with equality checks pushed down to SQL.
- **Auth**: your application's users: accounts, sessions, OAuth, TOTP MFA, the organizations they create, roles and permissions.
- **Realtime**: channels with broadcast, presence and history.
- **Flows**: a visual workflow editor with 58 blocks.
- **Code**: Python functions and custom routes.
- **Automation**: events, queues, jobs and the scheduler, plus webhooks in and out.
- **Platform services**: storage with signed URLs, cache, mail, secrets and API keys.
- **Ops**: Atlas API docs and observability.

Sillo provides the primitives: routing, validation, auth, Record (the ORM), events, queue,
scheduler, cache, storage, mail, security, OpenAPI, Inertia and Wire. Pawabase is the product
built on top of them, and adds code only where Sillo stops. The gaps found along the way are
listed in [`docs/sillo-gaps.md`](docs/sillo-gaps.md).

## Two services

Pawabase is **two processes**:

| Service | Port | What it does |
|---|---|---|
| **Server** (`pawabase_server`) | **8080** public, 8001 internal | One process: identity, the management plane, the data plane, storage, functions, flows, events, realtime, the queue worker and the scheduler, against one database. Port 8080 is the API your clients use, behind the edge (API keys, firewall, CORS, rate limits, quotas). Port 8001 is the same application without the edge, for the services that manage it; it is never published. |
| **Studio** (`studio`) | **8090** | The control plane: Sillo, sillo-inertia and React. It manages the server over port 8001 with short-lived service tokens, and has no sign-in unless you configure one, so it listens on localhost by default. |

Inside the server the parts call each other as Python, not over a network. See
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the design and how each capability maps onto Sillo.

```sh
python -m pawabase_server            # serve (the default); also `migrate`, `check`, `version`
python -m studio                     # Studio
```

## Develop with Docker

```sh
docker compose -f docker-compose.dev.yml up
```

Runs the server and Studio plus Postgres, Redis and MinIO, with the repository bind-mounted into
the containers. Edit Python and the server restarts in a second; edit Studio's front end and Vite
hot-reloads the browser. No `.env` needed. Studio is on <http://localhost:8090> (it opens
straight into the backend), the public API on `:8080`. Rebuild (`up --build`) only when
`pyproject.toml` or `uv.lock` changes.

## Run it with Docker (production)

To try it on your machine with ready-made settings:

```sh
cp .env.test .env
docker compose up -d
```

Then open Studio at <http://localhost:8090>. The values in `.env.test` are public, so use them
only locally.

For a real deployment, start from `.env.example` instead:

```sh
cp .env.example .env
# Fill in the secrets: openssl rand -hex 32 for each.
docker compose up -d
```

Open Studio at <http://localhost:8090>. A fresh runtime starts with a `development` environment;
add `staging` and `production` from Studio's Environments page. Every environment has its own
publishable and secret key (Keys).

Studio is local-only by default. Set both `PAWABASE_STUDIO_USERNAME` and
`PAWABASE_STUDIO_PASSWORD` to show a self-hosted Studio login form; successful
sign-ins receive an HttpOnly session cookie. This is separate from Pawabase
Cloud. Name the backend with `PAWABASE_PROJECT_NAME`.

Optional `.env` limits default to `0` (disabled): daily/monthly requests,
active WebSockets, upload bytes, users per environment, environments, and API
keys per environment. Request quotas use Redis-backed token leases, so normal
requests do not query Redis or the database; tune the bounded lease
size with `PAWABASE_REQUEST_QUOTA_RESERVATION`.

Clients call the public entry point:

```sh
curl http://localhost:8080/rest/v1/todos -H "apikey: <publishable key>"
curl -X POST http://localhost:8080/auth/v1/signup -H "apikey: <publishable key>" \
  -H "content-type: application/json" -d '{"email":"ada@example.com","password":"Str0ng!pass"}'
```

The bundled Postgres holds the platform's own data, your application's users, and default resource tables.
Default resource tables are namespaced by environment; configure
`database_url` in Studio → Settings → Infrastructure when an environment should use its own
database. Redis carries the queue, events, cache and rate limits. The
same goes for storage and mail (SMTP). File storage runs on a bundled MinIO by default (built
from MinIO's final community source release, rather than its withdrawn public registry image); set
`PAWABASE_STORAGE_ENDPOINT` and credentials in `.env` to use AWS S3, R2, B2, Spaces or any other
S3-compatible service instead, or configure storage per environment in Studio. Reference
credentials as `secret://NAME` so they are stored encrypted.

The container applies database migrations on start (Sillo Record migrations). Set
`PAWABASE_MIGRATE=false` to run them yourself with `docker compose run --rm pawabase migrate`.

### In production

- Put TLS in front of the public port and Studio. Never publish port 8001. Set `PAWABASE_COOKIE_SECURE=true` and point
  `PAWABASE_PUBLIC_URL` / `PAWABASE_PUBLIC_GATEWAY_URL` at the public URL.
- `PAWABASE_APP_ENV=production` (the compose default) makes the server refuse to start while a
  development secret is still in place.
- The queue worker and the scheduler are processes of their own, never part of the server's event
  loop. `docker-compose.yml` runs each as a container (`worker`, `scheduler`); a single container
  (`docker-compose.external.yml`, or plain `pawabase-server serve`) starts and supervises them as
  child processes. To run more than one server, set Redis (events, cache, queues and realtime
  fan-out then span instances), set `PAWABASE_WORKER_MODE=external` on the servers, and run
  `pawabase-server worker` wherever you need capacity and `pawabase-server scheduler` exactly once.
- Mount your code (`functions/`, `policies/`, `routes.py`) at `/code`. The compose file mounts
  `code/`.

## Develop without Docker

Requires [uv](https://docs.astral.sh/uv/) and Node 20+.

```sh
uv sync
scripts/dev.sh                  # the server and Studio on SQLite: API on :8080, Studio on :8090
STUDIO_VITE=1 scripts/dev.sh    # …with Studio's front end from `npm run dev`
```

Open Studio on <http://localhost:8090>: it starts in the `development` environment.

Tests and lint:

```sh
uv run pytest -q tests
uv run ruff check . && uv run ruff format --check .
```

## Configuration

Infrastructure and limits (database, storage, mail, quotas) are set with environment
variables, never through the API. `PAWABASE_<KEY>` applies to every environment and
`<ENV>_<KEY>` overrides it for one (`PRODUCTION_STORAGE_BUCKET`, `STAGING_MAX_USERS`).
See `.env.example` for the keys. Studio's Settings page shows what is in effect and where
each value came from; `GET /platform/v1/capacity` reports what a deployment is using, and
`docker-compose.external.yml` runs the one container against your own Postgres, Redis and S3 (TLS to a database that signs with its own certificate is trusted through `PAWABASE_EXTRA_CA_B64`).

**Upgrading.** Stored `infra.database_url` and `infra.storage` still work and are reported
as deprecated; `python -m pawabase_server.environments.export_config` prints them as variables. **Mail is the one
break:** `infra.mail` is now ignored, so set `PAWABASE_MAIL_*` (or `<ENV>_MAIL_*`) before
upgrading or mail is only logged. Secrets are now bound to their environment; existing
ciphertext still opens, and `python -m pawabase_server.environments.reseal` rebinds it.

## Function isolation

Deployed functions run in worker processes of their own, started on demand, reused while the deployment is live and stopped when idle. A function that
loops forever (the timeout kills its process), uses too much memory (`PAWABASE_SANDBOX_MEMORY_MB`, Linux) or calls `exit()` stops only itself. The worker gets
no secret, no database address and no network: `ctx.runtime` reaches the platform over a pipe, as the caller the API chose, and outbound HTTP leaves from the
API through its guard. A warm call costs under a millisecond more; the first call after a deploy or an idle period waits about a second for the worker.

What changes for a function: module-level state (a cache in a global) is per worker, and up to `PAWABASE_SANDBOX_WORKERS` workers serve a deployment at once, so
keep shared state in the database or `ctx.runtime.cache_*`. Code mounted from the `code/` folder is the platform's own and still runs in the API process.

To run deployed functions on the API's event loop instead, set `PAWABASE_FUNCTION_ISOLATION=inprocess` (or `<ENV>_FUNCTION_ISOLATION=inprocess` for one
environment).

Not yet covered: the API still imports a deployment's code to read its function list, so code that runs when the file is imported runs in the API.
This is isolation from crashes, hangs and memory, not yet a sandbox for hostile code (no filesystem or network namespace).

## Related repositories

| Repository | What it is |
| --- | --- |
| [pawabase-python](https://github.com/Pawabase/pawabase-python) | Python kit, CLI and emulator (`pip install pawabase`); this platform depends on it |
| [pawabase-js](https://github.com/Pawabase/pawabase-js) | TypeScript client (`@pawabase/client`) |
| [pawabase-docs](https://github.com/Pawabase/pawabase-docs) | Documentation (Mintlify) |
| [pawabase-website](https://github.com/Pawabase/pawabase-website) | Marketing site |

## Repository layout

```
pawabase_server/   the server: one package
  app.py server.py cli.py settings.py platform.py   the assembly, listeners, command, settings, the long-lived services
  core/            reusable foundation: ids, tokens, crypto, context, auth, errors, telemetry, clients, ...
  db/              database connection and commands, fields, models/, migrations/ (one chain)
  identity/        accounts, sessions, MFA, OAuth, roles, invitations, recovery links, identity backup
  environments/    an environment's definitions, settings, releases, blueprints, config export
  functions/       function deployments, execution, runtime, sandbox/
  compiler/        resources and routes compiled into Sillo routes; the data plane's dispatch
  data/            SQL across SQLite, PostgreSQL and MySQL; resource changes
  automation/      queue worker, scheduler, event processing, webhooks, jobs/
  observability/   analytics, metrics, logs, alarms, system health, status page
  backup/          environment backups and saved snapshots
  edge/            the public entry point: screening, keys, policy, admission, routing, quotas, limits, hostnames
  realtime/        channels, presence, fan-out, and the socket protocol
  flows/ policies/ the flow engine and blocks, the policy engine
  infra/           mail, outbound HTTP, object storage drivers
  routes/          HTTP routes: platform, data, identity, realtime, public
studio/            the control plane: its own service (Python server + frontend/)
tests/             one suite: foundation, platform, identity, realtime, edge, studio, server
code/              project code mounted into the server (empty; `.deployments` is git-ignored)
docker/            entrypoint, dev image, MinIO image
docker-compose.yml           the full stack: server, Studio, Postgres, Redis, MinIO
docker-compose.external.yml  the server and Studio, against infrastructure you run
docker-compose.dev.yml       development (live reload)
docs/              design notes, Sillo gaps
```

## Upgrading from the multi-service layout

Earlier versions ran the API, Akountz, Angula and the gateway as separate containers, with
identity in a second database. The server is one process with one database now (Studio is still
its own service):

- Run the new image against the **platform** database. `migrate` applies one new migration
  (`0014_identity`) that creates the identity tables beside the platform ones.
- Identity data lived in the separate `akountz` database. Copy its tables (`akz_*`, `permissions`,
  `perm_*`, `user_permissions`, `jwt_tokens`, `token_blacklist`) into the platform database before
  first use; the names and columns are unchanged. `PAWABASE_AKOUNTZ_DATABASE_URL` is no longer read.
- `PAWABASE_API_URL`, `PAWABASE_AKOUNTZ_URL`, `PAWABASE_ANGULA_URL`, `PAWABASE_GATEWAY_URL` and
  `PAWABASE_STUDIO_URL` are gone, as are `PAWABASE_PROCESSES` and `PAWABASE_STUDIO=off`. Studio
  finds the server through `PAWABASE_SERVER_URL`. `docker-compose.hosted.yml` and
  `docker-compose.cloud.yml` are replaced by `docker-compose.yml` and
  `docker-compose.external.yml`.

## License

BSD-3-Clause
