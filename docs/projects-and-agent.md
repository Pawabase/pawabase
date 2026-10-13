# Projects, project databases and the runtime agent

## The model

```
runtime (one Pawabase installation)
└── project            (slug, id, status; independent of any user)
    ├── database binding   exactly one: where the project's application data lives
    └── environments       development, staging, production…   (a schema, or a SQLite file, each)
```

* **Environments are not projects.** An environment belongs to one project and is separated from its siblings by a PostgreSQL schema
  inside the project's database (or a file, on SQLite). There is never a database per environment.
* **One database per project.** Projects may share a database *server*; they never share a database, and each gets its own role.
* **`Environment.name` is the runtime-wide key**, the value every table files its rows under (`env` columns, Redis keys, bucket
  prefixes). It stays unique and never changes. What people see and what is unique *per project* is `Environment.slug`. For the default
  project the two are equal; for another project the key is `<project-slug>-<env-slug>`.
* **Core has no developer accounts.** Who may administer a runtime is decided outside it: the local command line, or the Cloud agent.
  Application Auth is only for the application's own users.

## The default project

On start-up the server makes sure `DEFAULT_PROJECT` (default `default`) exists, is bound to a database, and owns every environment that
predates projects, each in the schema it already used. Nothing is overwritten and nothing is moved:

* an existing project, binding or environment is never replaced;
* if `DEFAULT_DATABASE_URL` differs from `PAWABASE_DEFAULT_DATA_URL` (where existing environments keep their data), nothing is attached
  and the conflict is logged: attaching would leave the data behind.

## Command line (no sign-in; it needs the installation's configuration, which is the same authority as running the server)

```sh
pawabase-server project create ecommerce
pawabase-server project list | show ecommerce
pawabase-server project db bind ecommerce --database-url-env ECOMMERCE_DATABASE_URL
pawabase-server project db test ecommerce
pawabase-server project db rotate ecommerce --database-url-env ECOMMERCE_DATABASE_URL_NEW
pawabase-server env create production --project ecommerce
pawabase-server env list --project ecommerce
pawabase-server project init        # what start-up does, on demand
pawabase-server project delete ecommerce   # only when it has no environments
```

A binding is stored as `env:NAME` (the address is a variable you manage) or `enc:…` (sealed with the installation's key). The address is
never returned by an API or written to a log. Binding a second, different database to a project needs `--rebind` and moves no data.

## The runtime agent (Cloud-managed runtimes only)

`pawabase-server agent` polls the control plane for instructions, carries them out through the same project manager the command line
uses, and reports. It is outside the request path: stop it and every project keeps serving.

| Variable | Meaning |
|---|---|
| `PAWABASE_AGENT_URL` | Control plane base URL (https; http only for localhost outside production) |
| `PAWABASE_AGENT_API_KEY` | The control plane's publishable key (its gateway requires one) |
| `PAWABASE_AGENT_ID` / `PAWABASE_AGENT_TOKEN` | The agent's identity and its secret (never sent; it signs with it) |
| `PAWABASE_AGENT_POOL` | The runtime pool it serves. It refuses instructions for any other |
| `PAWABASE_AGENT_SECRET_SEED` | Derives managed database role passwords, so no instruction carries one |
| `PAWABASE_AGENT_DATABASES` | JSON: `{"pool-db": "postgres://{role}:{password}@host:6432/{database}?ssl=require"}` database hosts it may use |

Allowed operations: `project.create`, `project.update`, `project.bind_database`, `project.delete` (empty projects only),
`environment.create`, `project.status`. Anything else is refused. Every instruction is signed, expires, names its pool, and is recorded by
id so a repeat is answered rather than done twice.

## Sharing one Redis

Set `PAWABASE_NAMESPACE` to a value unique to the runtime. It prefixes the platform event channel, realtime fan-out channel, caches,
rate-limit counters and queue names. Left empty, names are what they always were.
