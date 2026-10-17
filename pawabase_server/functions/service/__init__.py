"""The functions runtime: where deployed functions run, as a service of its own or inside the server.

::

    client ──► gateway (edge) ──► server: auth, policy, quotas, metering ──► functions runtime ──► worker (Deno / Python)
                                         ▲                                            │
                                         └──────── ctx.runtime calls, run reports ◄────┘   (signed with the invocation's grant)

**Two kinds of function** run here. ``fn()``/``@function`` functions take JSON and return JSON (``POST /functions/v1/<name>``), one call
per worker at a time, as before. **HTTP functions** (``functions/<name>/index.ts``) answer any method and path under
``/functions/v1/<name>`` with a ``Response``, many requests per worker, streamed both ways, the way Supabase Edge Functions do.

**Where it runs.** With ``PAWABASE_FUNCTIONS_RUNTIME_URL`` set, the server forwards every invocation to the runtime service
(``python -m pawabase_server.functions.service``), which can be scaled, restarted and resourced on its own and holds no database
credentials and no secrets of its own: what a function may do on the platform it asks the server for, with a short-lived **grant** that
names the environment, the branch and the caller the invocation was authorised for. Without the URL the same engine runs inside the
server process (:class:`~pawabase_server.functions.service.runtime.FunctionsRuntime`), which is what a single-process installation and
the tests use. Both paths go through the same code; only the transport differs.

Modules:

``grants``      the signed identity of one invocation
``bridge``      answering a worker's ``ctx.runtime`` calls: in-process, or through the server
``egress``      a function's ``fetch()``, made by the runtime through the outbound guard (DNS-rebinding-safe)
``http_worker`` one HTTP function's Deno worker, multiplexed and streamed
``http_pool``   workers per function: scaling, warm workers, draining, reaping, statistics
``runtime``     the engine: both pools, worker environments, run reports
``remote``      the server's client of a runtime service
``app``         the runtime service's HTTP interface
``reports``     what one run reports, and where it is recorded
"""
