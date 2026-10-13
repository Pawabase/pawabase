"""The runtime agent: the part of a Cloud-managed runtime that applies provisioning instructions.

It is a process of its own, outside the application's request path: clients, the edge, auth, data, storage, realtime, functions and the
queue never wait on it, and a runtime keeps serving while it is stopped. It dials out to the control plane, so the runtime needs no
inbound management endpoint.
"""
