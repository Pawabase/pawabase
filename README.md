# Pawabase

**Bring your infrastructure, build your backend.**

Pawabase is a backend you run yourself. You bring a database, object storage and mail credentials; Pawabase gives
you the backend around them, ready to use and managed from one place, Studio. Define your data, decide who can see
what, and your app gets a secure API, sign-in, files, realtime and background work, without assembling any of it.

One Pawabase installation is one project. Start it, open Studio, and you are managing that backend: no accounts to
create first, and no vendor in the middle.

## What you get

- **Data and an API.** Describe a table once and get a REST API for it, with filtering, sorting, relations, validation
  and generated API docs. Import and export CSV or JSON, and see how your data connects in Topology.
- **Sign-in and teams.** Email and password, social sign-in, two-step verification, organizations, roles and
  invitations, all readable from your permission rules.
- **Permissions you can read.** Write who can list, read, create, update and delete in plain conditions. They are
  enforced on every request and every file.
- **Files.** Buckets with the same rules as your data, uploads, and expiring download links, on local disk or any
  S3-compatible storage.
- **Realtime.** Channels with broadcast, presence and recent history, over WebSockets.
- **Logic and automation.** Write your own functions in Python, build workflows visually, and react to events with
  schedules, webhooks in and out, and retried background jobs.
- **Environments.** Development, staging and production side by side, each with its own keys, settings and data,
  with releases and branches for safe changes.
- **Operations.** Metrics, logs and alarms that email you, a public status page, backups and snapshots, secret and
  key rotation, custom domains and a firewall.

## Try it in two minutes

You need Docker.

```sh
cp .env.test .env
docker compose up -d
```

Open **Studio** at <http://localhost:8090>. It starts with a `development` environment and opens straight into your
backend. Your API is at <http://localhost:8080>.

The values in `.env.test` are public, so use them only on your own machine.

## Use your API

Every environment has a publishable key for your apps and a secret key for your servers (Studio, API keys).

```sh
# Read a resource
curl http://localhost:8080/rest/v1/todos -H "apikey: <publishable key>"

# Sign someone up
curl -X POST http://localhost:8080/auth/v1/signup \
  -H "apikey: <publishable key>" -H "content-type: application/json" \
  -d '{"email":"ada@example.com","password":"Str0ng!pass"}'
```

Client libraries:

- **Python**: `pip install pawabase`, also the CLI for deploying functions ([pawabase-python](https://github.com/Pawabase/pawabase-python))
- **TypeScript**: `@pawabase/client` ([pawabase-js](https://github.com/Pawabase/pawabase-js))

## Run it for real

```sh
cp .env.example .env
# Set the secrets: `openssl rand -hex 32` for each one.
docker compose up -d
```

- **Your infrastructure.** The default stack brings Postgres, Redis and MinIO. Point Pawabase at your own database,
  Redis and S3-compatible storage (AWS S3, R2, Backblaze B2, Spaces) with the variables in `.env.example`, or run
  `docker-compose.external.yml` for a host that already has them.
- **Secure by default.** In production mode Pawabase refuses to start while a development secret is still in place.
  Put TLS in front of the public port and Studio, set `PAWABASE_COOKIE_SECURE=true`, and turn on Studio's
  sign-in with `PAWABASE_STUDIO_USERNAME` and `PAWABASE_STUDIO_PASSWORD`.
- **Scale out.** Run more than one server by pointing them at the same Redis. The queue worker and the scheduler
  are separate processes you can place and scale on their own.
- **Limits.** Optional per-installation and per-environment limits on requests, connections, uploads, users and
  keys. They are off until you set them.
- **Configuration.** Infrastructure and limits are environment variables. `PAWABASE_<KEY>` applies everywhere and
  `<ENV>_<KEY>` overrides it for one environment, for example `PRODUCTION_STORAGE_BUCKET`. Studio's Settings page
  shows what is in effect and where each value came from.

Functions you deploy run in processes of their own, with memory and time limits, so one misbehaving function does
not take your backend down.

## Pawabase Cloud

Prefer not to run it yourself? Pawabase Cloud runs a project for you, with its own database, and bills by
subscription. It is in pre-launch.

## Documentation and related projects

| | |
| --- | --- |
| [pawabase-docs](https://github.com/Pawabase/pawabase-docs) | The documentation |
| [pawabase-python](https://github.com/Pawabase/pawabase-python) | Python library and CLI |
| [pawabase-js](https://github.com/Pawabase/pawabase-js) | TypeScript client |
| [pawabase-website](https://github.com/Pawabase/pawabase-website) | The website |

Upgrading an older installation? See [docs/upgrading.md](docs/upgrading.md).

## Contributing

```sh
docker compose -f docker-compose.dev.yml up    # live reload; Studio on :8090, API on :8080
uv run pytest -q tests
```

[ARCHITECTURE.md](ARCHITECTURE.md) explains how the code is organized.

## License

BSD-3-Clause
