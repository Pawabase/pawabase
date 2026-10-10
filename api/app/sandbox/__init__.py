"""Running a deployment's functions in processes of their own.

A function used to run on the API's event loop, with the API's memory and the API's database access: one that blocked the loop, ran out of memory or
called ``sys.exit`` took the requests of everyone else with it. With ``PAWABASE_FUNCTION_ISOLATION=process`` (or ``<ENV>_FUNCTION_ISOLATION``) a
deployment's functions run in a worker process instead:

* :mod:`app.sandbox.worker` is that process. It imports the deployment once, applies its limits, and answers invocations over its pipes.
* :mod:`app.sandbox.host` is the API's side: a pool of workers per deployment, started on demand, stopped when idle, killed on a timeout.

The worker has no credentials and no network of its own. ``ctx.runtime`` inside it is the kit's :class:`~pawabase.runtime.RemoteRuntime` over the pipe, and
the API runs every call (data, cache, events, secrets, outbound HTTP through the platform's guard) against the real runtime of that invocation.
"""

from .host import SandboxFailure, SandboxPool

__all__ = ["SandboxFailure", "SandboxPool"]
