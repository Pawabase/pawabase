# Pawabase

**Bring your infrastructure, build your backend.**

Pawabase is a self-hostable backend platform built on [Sillo](https://github.com/sillohq/core).
You bring the database, Redis, object storage, SMTP and OAuth credentials; Pawabase gives you
the backend around them, operated from one control plane:

- **Resources**: tables exposed as REST with filtering, sorting, relations and OpenAPI docs.
- **Policies**: JSON conditions, with equality checks pushed down to SQL.
- **Auth**: Akountz handles accounts, sessions, OAuth, TOTP MFA, organizations, roles and permissions.
- **Realtime**: Angula channels with broadcast, presence and history.
- **Flows**: a visual workflow editor with 58 blocks.
- **Code**: Python functions and custom routes.
- **Automation**: events, queues, jobs and the scheduler, plus webhooks in and out.
- **Platform services**: storage with signed URLs, cache, mail, secrets and API keys.
- **Ops**: Atlas API docs and observability.

Sillo provides the primitives: routing, validation, auth, Record (the ORM), events, queue,
scheduler, cache, storage, mail, security, OpenAPI, Inertia and Wire. Pawabase is the product
built on top of them, and adds code only where Sillo stops. The gaps found along the way are
listed in [`docs/sillo-gaps.md`](docs/sillo-gaps.md).

## Services

| Service | Port | What it does |
|---|---|---|
| Gateway | 8080 (public) | The only public entry point. It resolves API keys, signs the platform context, applies CORS and rate limits, and proxies HTTP and WebSockets. |
| Studio | 8090 (public) | The control plane: Sillo, sillo-inertia and React. |
| API | 8001 | Projects, environments, resources, routes, flows, functions, events, storage, keys, secrets and docs. |
| Worker | — | Queue workers: flows, functions, event processing, webhooks and mail. |
| Scheduler | — | Cron and interval schedules. Run exactly one. |
| Akountz | 8002 | Identity. |
| Angula | 8003 | Realtime. |

The services share one image and a small shared library (`pawabase_core/`), but run as separate
processes that talk over HTTP with audience-bound service tokens. See
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the design and how each capability maps onto Sillo.

## Develop with Docker

```sh
docker compose -f docker-compose.dev.yml up
```

Runs every service plus Postgres and Redis, with the repository bind-mounted into the
containers. Edit Python in any service or `pawabase_core/` and only that service restarts;
edit Studio's front end and Vite hot-reloads the browser. No `.env` needed. Studio is on
<http://localhost:8090> (`admin@pawabase.local` / `Pawabase!admin1`), the gateway on `:8080`.
Rebuild (`up --build`) only when a `pyproject.toml` or `uv.lock` changes.

## Run it with Docker (production)

To try it on your machine with ready-made settings:

```sh
cp .env.test .env
docker compose up -d
```

Then open Studio at <http://localhost:8090> and sign in as `admin@pawabase.test` /
`Pawabase!test1`. The values in `.env.test` are public, so use them only locally.

For a real deployment, start from `.env.example` instead:

```sh
cp .env.example .env
# Fill in the secrets: openssl rand -hex 32 for each, and an admin password
# with upper- and lower-case letters, a digit and a symbol.
docker compose up -d
```

Open Studio at <http://localhost:8090> and sign in with `PAWABASE_ADMIN_EMAIL` /
`PAWABASE_ADMIN_PASSWORD`. Create a project; it comes with `development`, `staging` and
`production` environments, each with a publishable and a secret key.

Clients call the gateway:

```sh
curl http://localhost:8080/rest/v1/todos -H "apikey: <publishable key>"
curl -X POST http://localhost:8080/auth/v1/signup -H "apikey: <publishable key>" \
  -H "content-type: application/json" -d '{"email":"ada@example.com","password":"Str0ng!pass"}'
```

The bundled Postgres holds the platform's own data, Akountz, and default resource tables.
Default resource tables are namespaced by project and environment; configure
`database_url` in Studio → Settings → Infrastructure when an environment should use its own
database. Redis carries the queue, events, cache and rate limits. The
same goes for storage (local or any S3-compatible service) and mail (SMTP). Reference
credentials as `secret://NAME` so they are stored encrypted.

Containers apply database migrations on start (Sillo Record migrations). Set
`PAWABASE_MIGRATE=false` to run them yourself with `docker compose run --rm api migrate`.

### In production

- Put TLS in front of the gateway and Studio. Set `PAWABASE_COOKIE_SECURE=true` and point
  `PAWABASE_PUBLIC_URL` / `PAWABASE_PUBLIC_GATEWAY_URL` at the public gateway URL.
- `PAWABASE_APP_ENV=production` (the compose default) makes every service refuse to start while
  a development secret is still in place.
- Scale `api`, `worker`, `akountz`, `angula` and `gateway` horizontally. Keep one `scheduler`.
- Mount your project code (functions, policies, routes) at `/code/<project>`. The compose file
  mounts `code/`.

## Develop without Docker

Requires [uv](https://docs.astral.sh/uv/) and Node 20+.

```sh
uv sync --all-packages
scripts/dev.sh                  # every service on SQLite, Studio on :8090
STUDIO_VITE=1 scripts/dev.sh    # …with Studio's front end from `npm run dev`
```

Sign in as `admin@pawabase.local` / `Pawabase!admin1`. Studio first asks you to create an
organization (every project lives in one, and you manage your team there); then create a project
with any reference.

Tests and lint:

```sh
uv run pytest -q                                   # pawabase_core
for s in api akountz angula gateway studio; do (cd $s && uv run pytest -q tests); done
uv run ruff check . && uv run ruff format --check .
```

## Related repositories

| Repository | What it is |
| --- | --- |
| [pawabase-python](https://github.com/Pawabase/pawabase-python) | Python kit, CLI and emulator (`pip install pawabase`); this platform depends on it |
| [pawabase-js](https://github.com/Pawabase/pawabase-js) | TypeScript client (`@pawabase/client`) |
| [pawabase-docs](https://github.com/Pawabase/pawabase-docs) | Documentation (Mintlify) |
| [pawabase-website](https://github.com/Pawabase/pawabase-website) | Marketing site |

## Repository layout

```
pawabase_core/        code shared by the services (not a package): policies, flow engine and blocks, schemas, service auth
api/                 Pawabase API, worker and scheduler
akountz/             identity
angula/              realtime
gateway/             public gateway
studio/              control plane (Python server + frontend/)
tests/               pawabase_core tests (each service has its own tests/)
code/                project code mounted into the API (empty; `.deployments` is git-ignored)
docker/              entrypoints, dev image, Postgres init
docker-compose.yml       production stack (one built image)
docker-compose.dev.yml   development stack (live reload)
docs/                design notes, Sillo gaps
```

## License

BSD-3-Clause
